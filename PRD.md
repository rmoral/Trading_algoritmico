# PRD — Automated Scalping Bot for US Equities via Interactive Brokers

Version: 1.0 · Status: Draft · Owner: Ruben Moral

> Companion to `CLAUDE.md`. `CLAUDE.md` is the operational contract for Claude Code. This document is the *why* and the *what*. When the two conflict, `CLAUDE.md` wins (it has more recent operational decisions).

---

## 1. Vision and goals

### 1.1 Vision
A robust, fully-automated trading system that executes short-horizon (seconds to minutes) strategies on US equities through Interactive Brokers, with the discipline of professional infrastructure (risk controls, observability, durable state) running on consumer-grade hardware and a single-operator budget.

### 1.2 Primary goals (in order)
1. **Capital preservation.** The system must lose less than a human would in the worst case. Risk controls are the product.
2. **Operational reliability.** Bot runs unattended during US market hours. Survives restarts, network blips, gateway resets.
3. **Validated edge before scaling.** No real money risked until backtest + ≥4 weeks paper match.
4. **Auditability.** Every decision the bot makes is logged, queryable, and explainable after the fact.
5. **Iterability.** Adding a new strategy is a self-contained job. The framework does not require rewrites for each new idea.

### 1.3 Explicit non-goals
- High-frequency trading (sub-millisecond). The system targets seconds-to-minutes; sub-second is out of scope.
- Multi-broker support. IBKR only.
- Crypto, futures, options. US equities only in v1.
- Mobile app. Telegram is the only user interface beyond CLI.
- Multi-user, multi-account. Single operator, single account.
- Discretionary trading aid. The bot trades; the human supervises.

### 1.4 Success metrics

| Metric | Target (Phase 5, paper) | Target (Phase 6, live) |
|---|---|---|
| Strategy Sharpe ratio (annualized, net of costs) | > 1.0 | > 0.8 |
| Max drawdown | < 15% | < 10% |
| Bot uptime during market hours | > 99% | > 99.5% |
| Mean fill latency (signal → submission) | < 200 ms | < 100 ms (NY VPS) |
| Reconciliation discrepancies vs IBKR | 0 unresolved | 0 unresolved |
| Live P&L vs paper P&L deviation (rolling 2 weeks) | n/a | < 20% |

---

## 2. Operator profile and context

- **Operator:** Single individual, technically literate, not a professional quant.
- **Location:** Andorra (Europe). 6h time zone offset from US Eastern.
- **Capital range:** discussed at $30k+ minimum equity in account (to clear PDT comfortably).
- **Time investment:** part-time research, daily quick check during market hours, weekly deeper review.
- **Risk tolerance:** willing to lose modest amount during paper-to-live transition to validate; not willing to risk major drawdown to test ideas.
- **Existing infrastructure:** MacBook Air M4 running OpenClaw (multi-agent AI), Telegram already integrated. Development happens here; production may migrate to NY VPS.

---

## 3. Functional requirements

### 3.1 IBKR connectivity (CAPA 1)
- FR-1.1 Connect to IB Gateway via TCP socket using `ib_insync`.
- FR-1.2 Distinguish paper (port 4002) and live (port 4001) environments via configuration.
- FR-1.3 Detect disconnection within 10 seconds; attempt reconnect with exponential backoff (1s, 2s, 4s, ... cap 60s).
- FR-1.4 On reconnect: re-subscribe to all required market data, reconcile open orders and positions against IBKR's view of the account.
- FR-1.5 Maintain in-memory account state (cash, buying power, positions, daily P&L) synced from IBKR account updates.
- FR-1.6 Heartbeat: emit a connection-health metric every 5 seconds. If silent > 30s, raise alert.

### 3.2 Market data (CAPA 2)
- FR-2.1 Subscribe to real-time NBBO ticks for tickers in the configured universe.
- FR-2.2 Aggregate ticks into bars at multiple resolutions in memory: 1s, 5s, 1min, 5min. (NOT persisted; live aggregation only.)
- FR-2.3 Persist raw ticks to TimescaleDB (configurable: full / sampled / off).
- FR-2.4 Compute live indicators: rolling VWAP (session), ATR(14), opening range high/low.
- FR-2.5 Universe manager: dynamic add/remove of subscribed tickers without restarting; respect IBKR's 100-line limit.
- FR-2.6 Detect and emit events: trading halt, halt resumption, LULD band approach.

### 3.3 Strategy engine (CAPA 4)
- FR-3.1 Strategy interface (`Strategy` ABC) with `on_bar`, `on_tick`, `on_event` hooks.
- FR-3.2 Strategies emit `Signal` objects; never call broker directly.
- FR-3.3 Multiple strategies run concurrently, each on its own universe subset.
- FR-3.4 Per-strategy configuration loaded from YAML at startup.
- FR-3.5 Per-strategy enable/disable via Telegram command without restart.
- FR-3.6 First implemented strategy: ORB (see CLAUDE.md §6).

### 3.4 Execution (CAPA 3)
- FR-4.1 Order router converts `Signal` to IBKR orders.
- FR-4.2 Supported order types: `LimitOrder`, `StopOrder`, `StopLimitOrder`, `BracketOrder`. `MarketOrder` only in emergency-flatten path.
- FR-4.3 Default entry: marketable limit at `last ± N ticks` with auto-cancel after `X` seconds if unfilled.
- FR-4.4 Default stop: server-side StopOrder on IBKR (survives bot crash).
- FR-4.5 Default take-profit: server-side LimitOrder (survives bot crash).
- FR-4.6 Bracket: parent + stop + TP submitted atomically.
- FR-4.7 Track slippage per fill: `(fill_price - signal_price) / signal_price * 10000` bps.
- FR-4.8 Order state machine: `PENDING → SUBMITTED → (PARTIAL_FILLED) → FILLED | CANCELLED | REJECTED`. Persist every transition.

### 3.5 Risk management (CAPA 5)
- FR-5.1 Every order passes through `RiskManager.approve(order, context)` before submission.
- FR-5.2 Hard limits (refuse + log + alert):
  - max daily loss USD
  - max daily loss as % of starting equity
  - max open positions
  - max position size (USD and %)
  - max trades per day
  - max orders per minute
  - max spread (bps) at submission time
  - forbidden tickers
- FR-5.3 Soft limits (warn + log, allow):
  - approaching daily loss limit (e.g. 80% of cap)
  - unusual spread widening mid-day
- FR-5.4 Pre-trade checks:
  - is market open?
  - is ticker halted?
  - is it earnings day for this ticker?
  - is account in "restricted" status?
  - is required buying power available?
- FR-5.5 Circuit breaker: trip on N consecutive losses, intraday drawdown threshold, or external command. Trip = cancel all, flatten all, refuse new signals until manual reset.

### 3.6 Persistence (CAPA 0)
- FR-6.1 PostgreSQL is single source of truth for: orders, fills, signals, daily P&L, strategy state checkpoints.
- FR-6.2 TimescaleDB hypertable for tick data.
- FR-6.3 Redis for: live position cache, rate-limiting counters, pub/sub for monitoring dashboards.
- FR-6.4 Schema migrations via Alembic. Never edit production schema without a migration.
- FR-6.5 On startup, reconcile DB state against IBKR's view; if inconsistent, log + alert + refuse to take new signals until reconciled manually.

### 3.7 Monitoring (CAPA 6)
- FR-7.1 Telegram bot commands:
  - `/status` — connection, market open?, kill switch state
  - `/positions` — list open positions
  - `/pnl` — today's P&L, MTD P&L
  - `/orders` — open orders
  - `/strategies` — list strategies and enable/disable state
  - `/enable <strategy>` / `/disable <strategy>`
  - `/kill` — confirm-then-trip circuit breaker
  - `/reset` — clear circuit breaker (requires confirmation)
- FR-7.2 Telegram alerts (push):
  - every fill (entry and exit)
  - every stop-loss hit
  - every rejection from broker
  - every reconnection event
  - daily summary at US market close
  - circuit breaker trips
  - unhandled exceptions
- FR-7.3 Prometheus metrics for Grafana:
  - connection state (gauge)
  - orders submitted / filled / cancelled / rejected (counters)
  - fill latency (histogram)
  - slippage in bps (histogram)
  - current P&L (gauge)
  - open positions count (gauge)
- FR-7.4 Grafana dashboard: at minimum, P&L chart, positions table, latency histogram, error rate, connection state.

### 3.8 Backtesting (separate path)
- FR-8.1 Use `nautilus_trader` engine.
- FR-8.2 Same `Strategy` class runs in backtest and live. Backtester injects historical data; live engine injects real-time data.
- FR-8.3 Commission model: IBKR Pro tiered (`$0.0035/share, $0.35 minimum, max 1% of trade value`).
- FR-8.4 Slippage model: configurable; default = half-spread × `slippage_factor` (default 0.5) for marketable orders, 0 for fully-filled limit orders.
- FR-8.5 Walk-forward analysis: in-sample / out-of-sample split with configurable windows.
- FR-8.6 Output: tearsheet (HTML) with equity curve, drawdown, monthly returns heatmap, trade distribution, Sharpe / Sortino / Calmar / profit factor / win rate, average win / loss / R-multiple.
- FR-8.7 Backtest results persisted with config snapshot (which strategy version, which parameters, which data range).

---

## 4. Non-functional requirements

| Category | Requirement |
|---|---|
| Performance | Signal-to-submit latency p99 < 500 ms (dev Mac), < 150 ms (NY VPS) |
| Reliability | Auto-recovery from: gateway disconnect, gateway daily restart, Postgres restart, Redis restart |
| Security | No secrets in code or git. `.env` permissions 0600. 2FA on IBKR account. SSH key auth only on VPS. |
| Observability | Every order traceable end-to-end from signal to fill via correlation ID |
| Cost | Infra budget < $200/month (excluding commissions). Owner-approved increases only. |
| Maintainability | New strategy can be added in < 1 day by someone familiar with the codebase |
| Portability | Bot runs on macOS (dev) and Linux (prod). Docker for infra services. |

---

## 5. Data model (logical)

Detailed SQL in `infra/postgres/init.sql` and Alembic migrations. Logical model:

**ticks** (TimescaleDB hypertable, partitioned by time)
- ts (PK part), symbol (PK part), bid, ask, last, bid_size, ask_size, last_size, exchange

**bars** (optional, derived from ticks if persisted)
- ts, symbol, resolution, open, high, low, close, volume, vwap

**signals**
- id (UUID PK), ts, strategy, symbol, side, score, suggested_qty, stop_loss, take_profit, metadata (JSONB)

**orders**
- id (UUID PK), ib_order_id (unique), signal_id (FK), ts_created, ts_submitted, ts_filled, ts_cancelled, symbol, side, qty, order_type, limit_price, stop_price, time_in_force, status, parent_order_id (for brackets)

**fills**
- id (UUID PK), order_id (FK), ts, qty, price, commission, exchange, exec_id (from IBKR)

**positions** (current state, but also versioned via inserts)
- id, ts, symbol, qty, avg_price, unrealized_pnl, realized_pnl_today

**pnl_daily**
- date (PK), starting_equity, ending_equity, gross_pnl, commissions, net_pnl, n_trades, n_wins, n_losses, max_dd_intraday

**risk_events**
- id, ts, event_type, severity, message, context (JSONB), action_taken

**reconciliation_log**
- id, ts, expected (JSONB), actual (JSONB), discrepancies (JSONB), resolved_at

---

## 6. External dependencies and integrations

| Service | Purpose | Cost | Critical path? |
|---|---|---|---|
| Interactive Brokers (Pro account) | Broker, order routing, real-time data | Commissions + ~$15/mo data | Yes |
| Polygon.io (Starter) | Historical tick data for backtest | $29/mo | No (development only) |
| Telegram (BotFather) | Notifications + commands | Free | Yes |
| GitHub | Code hosting + CI | Free tier | No (could move) |
| QuantVPS / AWS us-east-1 | Production hosting (later) | $50–150/mo | Phase 6+ |

---

## 7. Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| PDT rule blocks intended frequency | Med | High | Phase 0 explicit confirmation with IBKR before any Phase 5 commitment |
| Backtest looks great, live underperforms | High | High | Forward-test in paper ≥4 weeks; scaled live rollout; daily live-vs-paper variance check |
| Edge does not exist for retail scalping | Med | High | Accept this outcome as the backtest's job to discover. Have swing-trading pivot plan ready. |
| Gateway 2FA breaks unattended | Low | High | IBC + IBKR Mobile authenticator tested in Phase 1; fallback alert if login fails |
| Network blip during a position | Med | Med | Server-side bracket orders (stop and TP live on IBKR) |
| Bug in risk manager allows oversized order | Low | Catastrophic | Property-based tests with Hypothesis; hard caps in IBKR account preferences as belt-and-suspenders |
| Commission costs exceed edge | Med | High | Model in backtest, monitor in live, accept that strategies with low edge per trade may not survive |
| Slippage in live > backtest assumption | Med | High | Continuous slippage tracking; alert if exceeds modeled value sustainably |
| Data feed lag/glitch causes bad fills | Low | Med | Cross-check IBKR feed with secondary source on high-conviction setups |
| Operator unavailable during a crash | Med | Med | Telegram alerts + server-side stops mean unsupervised positions are protected; weekly review catches structural issues |

---

## 8. Open questions to resolve before Phase 1 exit

1. PDT applicability to operator's IBKR entity — pending IBKR response.
2. Production hosting: QuantVPS vs IBKR-hosted VPS vs AWS us-east-1 — defer to Phase 5.
3. Should we use IBKR-provided market data only, or add a secondary feed (Polygon) for live cross-check? — defer to Phase 4 based on observed data quality.
4. Tax/reporting requirements for Andorran resident trading US equities — out of scope for the bot itself; operator's accountant problem.

---

## 9. Out of scope for v1 (parking lot)

- Multiple strategies running on the same ticker (signal merging / conflict resolution)
- ML-driven signal generation
- Options strategies
- Pairs trading / stat arb
- Multi-asset portfolio construction
- Tax-aware order routing
- Public API or web UI

These may be considered after Phase 6 stable.

---

## 10. Glossary

See `CLAUDE.md` §12.
