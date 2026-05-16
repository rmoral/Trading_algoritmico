# PRD — Automated Scalping Bot for US Equities via Interactive Brokers

Version: 2.0 · Status: Draft · Owner: Ruben Moral

> Companion to `CLAUDE.md`. `CLAUDE.md` is the operational contract for Claude Code. This document is the *why* and the *what*. When the two conflict, `CLAUDE.md` wins (it has more recent operational decisions).

---

## 1. Vision and goals

### 1.1 Vision
A robust, fully-automated trading system that executes short-horizon (seconds to minutes) operations on **a single US equity selected daily by the operator**, through Interactive Brokers, controlled and observed from a **web application**. Discipline of professional infrastructure (risk controls, observability, durable state, audit log) running on consumer-grade hardware and a single-operator budget.

### 1.2 Primary goals (in order)
1. **Capital preservation.** The system must lose less than a human would in the worst case. Risk controls are the product.
2. **Operational reliability.** Bot runs unattended during US market hours. Survives restarts, network blips, gateway resets.
3. **Validated edge before scaling.** No real money risked until ≥4 weeks of clean paper operation.
4. **Auditability.** Every decision the bot makes is logged, queryable, and explainable after the fact.
5. **Operator control.** The operator configures policies, picks the asset, and reviews operations through the web app. The bot trades; the human supervises.

### 1.3 Explicit non-goals
- High-frequency trading (sub-millisecond). Target is seconds-to-minutes.
- Multi-broker support. IBKR only.
- Crypto, futures, options. US equities only in v1.
- Multi-strategy / multi-position concurrent execution. **One position, one asset, one strategy at a time.**
- Multi-user, multi-account.
- Backtesting infrastructure. Deferred to post-v1 once live operation is stable.
- Holding positions overnight. All positions are flattened before market close.
- Discretionary trading aid. The bot trades autonomously; the human configures and supervises.

### 1.4 Success metrics

| Metric | Target (Phase 4, paper) | Target (Phase 5, live) |
|---|---|---|
| Win rate (after costs) | > 50% | > 50% |
| Average R-multiple realized | > 1.0 | > 1.0 |
| Daily P&L (paper) | net positive on rolling 4-week basis | n/a |
| Max intraday drawdown | < `max_daily_loss_usd` | < `max_daily_loss_usd` |
| Bot uptime during market hours | > 99% | > 99.5% |
| Mean fill latency (signal → submission) | < 200 ms (dev Mac) | < 100 ms (NY VPS) |
| Reconciliation discrepancies vs IBKR | 0 unresolved | 0 unresolved |
| Live P&L vs paper P&L deviation (rolling 2 weeks) | n/a | < 20% |

---

## 2. Operator profile and context

- **Operator:** single individual, technically literate, not a professional quant.
- **Location:** Andorra (Europe). 6 h offset from US Eastern.
- **Capital:** $150,000 USD in the operating account (above PDT minimum by a wide margin).
- **Currency:** all trading is USD-denominated; the dashboard may display an EUR equivalent for reference.
- **Time investment:** part-time supervision, daily quick check during market hours, weekly deeper review.
- **Risk tolerance:** willing to lose small amounts during paper-to-live transition; not willing to accept major drawdowns to test ideas.
- **Existing infrastructure:** MacBook Air M4 for development, Telegram already integrated for ops alerts. Production migrates to a NY-region VPS in Phase 5.

---

## 3. Functional requirements

### 3.1 IBKR connectivity (CAPA 1)
- FR-1.1 Connect to IB Gateway via TCP socket using `ib_insync`.
- FR-1.2 Distinguish paper (port 4002) and live (port 4001) environments via configuration; live trading additionally gated by `LIVE_TRADING=true` env var.
- FR-1.3 Detect disconnection within 10 s; reconnect with exponential backoff (1 s, 2 s, 4 s, … capped at 60 s).
- FR-1.4 On reconnect: re-subscribe market data, reconcile open orders and the open position against IBKR's view.
- FR-1.5 Maintain in-memory account state (cash, buying power, position, daily P&L) synced from IBKR account updates.
- FR-1.6 Heartbeat: emit a connection-health metric every 5 s. Silent > 30 s → alert.

### 3.2 Market data (CAPA 2)
- FR-2.1 The operator selects the **active asset for the day** from the web app. The bot subscribes to that single ticker.
- FR-2.2 Subscribe to bars at **1 minute, 5 minutes, 15 minutes** resolutions via IBKR's `reqHistoricalData` with `keepUpToDate=True`. Pre-load premarket + last 2 h on startup, then receive live updates.
- FR-2.3 Subscribe to NBBO ticks for spread monitoring and entry timing.
- FR-2.4 Persist completed bars to Postgres; ticks optionally (default off in v1, configurable).
- FR-2.5 Compute live indicators on rolling windows: session VWAP, EMA20/EMA50/EMA200, ATR(14), volume profile by price bucket.
- FR-2.6 **S/R detector:** continuously scan completed 1m and 5m bars within `sr_lookback_minutes`, identify level candidates (local extrema), compute strength score per `CLAUDE.md` §6. Persist detected levels to `sr_levels`. Update score in real time as new bars arrive.
- FR-2.7 Detect and emit events: trading halt, halt resumption, LULD band approach.

### 3.3 Strategy engine (CAPA 4)
- FR-3.1 The bot operates as a **state machine**: `CERRADA → ABRIENDO → ABIERTA → CERRANDO → CERRADA`. State transitions are persisted to Postgres before the corresponding action.
- FR-3.2 **Single position invariant.** At any moment at most one position is in {ABRIENDO, ABIERTA, CERRANDO}.
- FR-3.3 **Discovery strategy** (active in CERRADA state, rolling 5-min window):
  - Evaluates S/R levels' strength.
  - On entry trigger (touch + reaction at a level), emits a `Signal` with side (LONG/SHORT), entry price band, target level, stop price, sizing (full vs 50% partial) per the S/R strength.
- FR-3.4 **Management strategy** (active in ABIERTA state):
  - Bracket order (entry + SL + TP) is live on IBKR server-side from the moment of fill.
  - Bot monitors fills, partial fills, and adjustments.
  - On SL or TP fill, transitions to CERRANDO → CERRADA.
- FR-3.5 Strategies emit `Signal` objects; never call the broker directly.
- FR-3.6 Per-strategy parameters loaded from `config_policies` at startup; live edits picked up on a configurable cadence (default 30 s).
- FR-3.7 Strategy can be **paused** via web app or Telegram `/pause`, without exiting an open position. Pause = no new entries.

### 3.4 Execution (CAPA 3)
- FR-4.1 Order router converts `Signal` → IBKR orders.
- FR-4.2 Supported order types: `LimitOrder`, `StopOrder`, `StopLimitOrder`, `BracketOrder`. `MarketOrder` only in: (a) kill switch path, (b) end-of-day forced flatten.
- FR-4.3 Default entry: marketable limit at `last ± N ticks` with auto-cancel after `X` seconds if unfilled (`X` configurable, default 5 s).
- FR-4.4 Default stop: server-side `StopOrder` on IBKR (survives bot crash).
- FR-4.5 Default take-profit: server-side `LimitOrder` (survives bot crash).
- FR-4.6 Bracket: parent + stop + TP submitted as one atomic unit (OCA group).
- FR-4.7 Track slippage per fill: `(fill_price − signal_price) / signal_price × 10000` bps. Persist.
- FR-4.8 Order state machine: `PENDING → SUBMITTED → (PARTIAL_FILLED) → FILLED | CANCELLED | REJECTED`. Persist every transition.
- FR-4.9 End-of-day: `no_new_entries_before_close_minutes` before close → stop scanning. `force_flatten_before_close_minutes` before close → if ABIERTA, market-flatten.

### 3.5 Risk management (CAPA 5)
- FR-5.1 Every order passes through `RiskManager.approve(order, context)` before submission. No bypass path.
- FR-5.2 Hard limits (refuse + log + alert):
  - `max_daily_loss_usd` (trip circuit breaker)
  - `max_position_size_usd`
  - `max_trades_per_day`
  - `max_orders_per_minute`
  - `max_spread_bps` at submission time
  - `forbidden_tickers`
  - Single-position invariant violation
- FR-5.3 Soft limits (warn + log, allow):
  - Approaching `max_daily_loss_usd` (e.g. 80% of cap)
  - Unusual spread widening mid-day
- FR-5.4 Pre-trade checks:
  - Market open?
  - Ticker halted?
  - Earnings day for this ticker?
  - Account in restricted status?
  - Buying power available?
  - Currently in CERRADA state?
- FR-5.5 **R-multiple filter:** reject signals with `r_multiple < min_r_multiple`.
- FR-5.6 **Commission filter:** reject signals with `commission / expected_profit > max_commission_pct_of_target`.
- FR-5.7 Circuit breaker: trip on `max_daily_loss_usd`, `consecutive_losses_limit`, `drawdown_pct_from_open`, external command (Telegram/web/env). Trip = cancel all, flatten, refuse new signals until manual reset.

### 3.6 Persistence (CAPA 0)
- FR-6.1 PostgreSQL is single source of truth for: orders, fills, signals, positions, daily P&L, S/R levels detected, configuration, audit log, risk events.
- FR-6.2 TimescaleDB hypertable for tick data and bar data (when persisted).
- FR-6.3 Redis for: live position cache, rate-limiting counters, pub/sub channel `bot:events` for the API/dashboard.
- FR-6.4 Schema migrations via Alembic. Never edit production schema without a migration.
- FR-6.5 On startup, reconcile DB state against IBKR's view; on inconsistency, alert + refuse new signals until reconciled manually.
- FR-6.6 **Audit log** for every config change made through the web app: who, when, before, after.

### 3.7 Monitoring (CAPA 6)
- FR-7.1 Telegram bot commands:
  - `/status` — connection, market open?, state machine state, kill switch state
  - `/position` — current open position (if any)
  - `/pnl` — today's P&L, MTD P&L
  - `/orders` — open orders
  - `/pause` / `/resume` — toggle entry scanning
  - `/kill` — confirm-then-trip circuit breaker
  - `/reset` — clear circuit breaker (requires confirmation)
- FR-7.2 Telegram alerts (push):
  - Every fill (entry and exit)
  - Every SL hit / TP hit
  - Every rejection from broker
  - Every reconnection event
  - Daily summary at US market close
  - Circuit breaker trips
  - Unhandled exceptions
- FR-7.3 Prometheus metrics for Grafana:
  - Connection state (gauge)
  - Orders submitted / filled / cancelled / rejected (counters)
  - Fill latency (histogram)
  - Slippage in bps (histogram)
  - Current P&L (gauge)
  - State machine state (enum gauge)
- FR-7.4 Grafana dashboard: P&L chart, state-machine timeline, latency histogram, error rate, connection state.

### 3.8 Web application (NEW in v2)
- FR-8.1 **Authentication:** login with username/password (Argon2id-hashed) + TOTP second factor. HTTPS-only. Session cookies `HttpOnly; Secure; SameSite=Lax`.
- FR-8.2 **Daily asset selection screen:** operator types/selects the ticker for today's session. Bot picks it up within 30 s. Changing mid-session is allowed only when in CERRADA state.
- FR-8.3 **Policy editor:** form-based editor for every runtime parameter listed in `CLAUDE.md` §7 (risk limits, S/R thresholds, profit range, end-of-day timing). All changes audit-logged. Validation enforced server-side via pydantic schemas. Hardcoded fallbacks in the bot are tighter than the values acceptable from the app.
- FR-8.4 **Dashboard (live view):**
  - Current state machine state, active asset, current position (if any) with live P&L.
  - Today's operations list with entry, exit, P&L, R realized, commissions.
  - Detected S/R levels for the active asset with their strength scores.
  - Daily P&L chart, cumulative P&L for the month.
  - Polling cadence 3–5 s.
- FR-8.5 **Operations history:** queryable table with filters (date range, side, ticker, outcome). Export to CSV.
- FR-8.6 **Account configuration screen:** IBKR connection parameters (host, port, client id, account id), Telegram bot token + chat id. Secrets stored encrypted at rest (`pgcrypto` or app-level AES-GCM with key from env).
- FR-8.7 **Kill switch button:** big red button, confirm-then-trip. Same effect as `KILL_SWITCH=true`.
- FR-8.8 **Read-only mode** for the dashboard when no operator session is active (publicly inaccessible — only reachable through auth).

### 3.9 Backtesting (deferred)
Backtesting is **out of scope for v1**. Validation pre-live is done through the ≥4-week paper forward-test (Phase 4). Backtesting infrastructure (`nautilus_trader` integration, historical data ingestion, tearsheet generation) will be revisited once Phase 5 live operation is stable.

---

## 4. Non-functional requirements

| Category | Requirement |
|---|---|
| Performance | Signal-to-submit latency p99 < 500 ms (dev Mac), < 150 ms (NY VPS) |
| Reliability | Auto-recovery from: gateway disconnect, gateway daily restart, Postgres restart, Redis restart |
| Security | No secrets in code or git. `.env` permissions 0600. 2FA on IBKR account. 2FA on web app. SSH key auth only on VPS. HTTPS-only on the dashboard. |
| Observability | Every order traceable end-to-end from signal to fill via correlation ID. Every config change audit-logged. |
| Cost | Infra budget < $200/month (excluding commissions). Owner-approved increases only. |
| Maintainability | New runtime parameter can be added (DB column + form field + bot read) in < 1 day. |
| Portability | Bot runs on macOS (dev) and Linux (prod). Docker for stateful infra. |

---

## 5. Data model (logical)

Detailed SQL in Alembic migrations. Logical model:

**ticks** (TimescaleDB hypertable, partitioned by time, optional)
- `ts` (PK part), `symbol` (PK part), `bid`, `ask`, `last`, `bid_size`, `ask_size`, `last_size`, `exchange`

**bars** (per resolution)
- `ts` (PK part), `symbol`, `resolution` (1m/5m/15m), `open`, `high`, `low`, `close`, `volume`, `wap`, `count`

**sr_levels**
- `id` (UUID PK), `ts_first_detected`, `symbol`, `price`, `kind` (support/resistance), `strength`, `components` (JSONB: c1..c5), `last_update_ts`, `broken_at` (nullable)

**signals**
- `id` (UUID PK), `ts`, `strategy`, `symbol`, `side`, `sr_level_id` (FK), `score`, `suggested_qty`, `stop_loss`, `take_profit`, `r_multiple`, `metadata` (JSONB)

**orders**
- `id` (UUID PK), `ib_order_id` (unique), `signal_id` (FK), `ts_created`, `ts_submitted`, `ts_filled`, `ts_cancelled`, `symbol`, `side`, `qty`, `order_type`, `limit_price`, `stop_price`, `time_in_force`, `status`, `parent_order_id` (for brackets)

**fills**
- `id` (UUID PK), `order_id` (FK), `ts`, `qty`, `price`, `commission`, `exchange`, `exec_id` (from IBKR)

**positions** (one row open at a time; closed positions retained with `closed_at`)
- `id`, `opened_at`, `closed_at` (nullable), `symbol`, `side`, `qty`, `avg_entry_price`, `avg_exit_price`, `realized_pnl`, `commissions`, `state` (enum: ABRIENDO/ABIERTA/CERRANDO)

**pnl_daily**
- `date` (PK), `starting_equity`, `ending_equity`, `gross_pnl`, `commissions`, `net_pnl`, `n_trades`, `n_wins`, `n_losses`, `max_dd_intraday`

**config_policies**
- `id`, `version`, `effective_from`, `effective_to`, `payload` (JSONB), `created_by`, `created_at`. Latest version with `effective_to IS NULL` is the live config.

**audit_log**
- `id`, `ts`, `actor`, `action`, `entity_type`, `entity_id`, `before` (JSONB), `after` (JSONB)

**risk_events**
- `id`, `ts`, `event_type`, `severity`, `message`, `context` (JSONB), `action_taken`

**reconciliation_log**
- `id`, `ts`, `expected` (JSONB), `actual` (JSONB), `discrepancies` (JSONB), `resolved_at`

**users** (web app)
- `id`, `username`, `password_hash` (Argon2id), `totp_secret_encrypted`, `created_at`, `last_login_at`, `is_active`

**sessions** (web app)
- `id`, `user_id`, `created_at`, `expires_at`, `last_seen_at`, `ip_address`

---

## 6. External dependencies and integrations

| Service | Purpose | Cost | Critical path? |
|---|---|---|---|
| Interactive Brokers (Pro account) | Broker, order routing, real-time data, historical bars | Commissions + ~$15/mo data | Yes |
| Telegram (BotFather) | Push alerts + ops commands | Free | Yes |
| GitHub | Code hosting + CI | Free tier | No (could move) |
| Domain registrar (e.g. Cloudflare/Namecheap) | DNS for web app | ~$10/yr | Phase 2+ |
| Cloudflare (free tier) | WAF, DDoS protection, DNS proxy in front of web app | Free | Phase 2+ |
| Caddy | Reverse proxy + automatic HTTPS | Free | Phase 2+ |
| NY-region VPS (e.g. QuantVPS, AWS us-east-1) | Production hosting | $50–150/mo | Phase 5+ |
| Sentry (free tier) | Bot + API error tracking | Free | Optional |
| Backblaze B2 | Daily encrypted Postgres backup | ~$6/TB/mo | Optional |

**Removed from v1 scope:** Polygon.io (would only be needed for backtesting, which is deferred). All historical data needs (premarket + intraday lookback for S/R) are covered by IBKR's `reqHistoricalData`.

---

## 7. Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| PDT rule blocks intended frequency | Low | High | Account ≥$150k clears PDT minimum by ~6×. Phase 0 confirms in writing with IBKR. |
| Edge does not exist for retail scalping on a single ticker | Med | High | Phase 4 paper test is the discovery. If no edge: pause, re-tune, or pivot. |
| S/R formula produces too few signals (under-active) | Med | Med | Configurable thresholds; relax `sr_strong/weak_threshold` and `min_r_multiple` based on observation. |
| S/R formula produces too many bad signals (over-active) | Med | High | Risk manager hard limits cap damage; tune thresholds. |
| Gateway 2FA breaks unattended | Low | High | IBC + IBKR Mobile authenticator tested in Phase 1; fallback alert if login fails. |
| Network blip during a position | Med | Med | Server-side bracket orders (SL and TP live on IBKR). |
| Bug in risk manager allows oversized order | Low | Catastrophic | Property-based tests with Hypothesis; hard caps in IBKR account preferences as belt-and-suspenders. |
| Commission costs exceed edge on small profits | Med | Med | `max_commission_pct_of_target` filter rejects trades where commission > 5% of expected profit. |
| Slippage in live > paper assumption | Med | High | Continuous slippage tracking; alert if exceeds modeled value sustainably. |
| Operator unavailable during a crash | Med | Med | Telegram alerts + server-side stops mean unsupervised positions are protected; weekly review catches structural issues. |
| Web app exposed and compromised | Low | High | Auth + TOTP + HTTPS + Cloudflare front; rate-limited login. Account number not displayed in plaintext; secrets encrypted at rest. |
| Configuration change crashes bot | Low | Med | All config validated server-side before persist; bot reads latest version, falls back to previous on parse failure. |

---

## 8. Open questions to resolve before Phase 1 exit

1. PDT applicability to operator's IBKR entity — pending IBKR response.
2. Production hosting choice — defer to Phase 5.
3. Domain name for the web app — operator decision before Phase 2.

---

## 9. Out of scope for v1 (parking lot)

- Multiple strategies running concurrently or on multiple assets.
- More than one open position at a time.
- ML-driven signal generation.
- Options, futures, crypto.
- Pairs trading / stat arb.
- Tax-aware order routing.
- Public API beyond the operator's authenticated dashboard.
- Mobile-native app (the web app is responsive but not native).
- Backtesting (deferred until post-v1; Phase 4 paper forward-test is the validation).
- Holding positions overnight.

These may be revisited after Phase 5 stable.

---

## 10. Glossary

See `CLAUDE.md` §12.
