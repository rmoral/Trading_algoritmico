# CLAUDE.md — Project Context for Claude Code

> This file is loaded automatically by Claude Code. It defines the project, its principles, conventions, and current state. **Read this completely before doing anything in this repo.**

---

## 1. What this project is

A fully-automated trading bot that executes **scalping operations on US equities (NYSE / Nasdaq)** via the Interactive Brokers TWS API, controlled and monitored through a **web application**. Holding times are seconds to minutes.

Two defining constraints set this bot apart from a generic algo framework:

- **One position at a time.** Globally. Two simultaneous open positions is a bug, not a feature request.
- **The operator picks the asset of the day** from the web app. The bot does not scan a universe — it focuses on a single ticker per session and extracts return through repeated entries on detected support/resistance levels.

The bot runs unattended during US market hours. The web application is the only user-facing surface beyond Telegram alerts.

**Owner:** Ruben Moral (single operator, ~25 years online sector experience, not a professional quant). Based in Andorra.

**This is NOT a research toy.** Real money will eventually flow through it. Code quality, testability, and risk controls matter more than features.

---

## 2. Critical principles — read before writing any code

These are non-negotiable. If a requested change violates one, push back and ask for clarification.

1. **Paper trading first, always.** No code path may submit live orders unless `LIVE_TRADING=true` env var is set AND the configured IB Gateway port is the live port (4001), not paper (4002). Belt and suspenders.

2. **Risk manager is in the critical path.** Every order must pass through `risk.RiskManager.approve(order)` before submission. No "fast path" bypasses it. If you find yourself wanting to bypass it for performance, the design is wrong — fix the design.

3. **Strategy code never touches the broker directly.** Strategies emit `Signal` objects. The execution layer is what talks to IBKR. This separation is what makes paper-vs-live behavior identical.

4. **Single position invariant.** At any given time the bot holds zero or one open position. Discovery logic must check the position state before emitting any entry signal. The position state machine (see §6) is sacred.

5. **Slippage and commissions are first-class citizens.** All sizing and target calculations must account for both. Defaults: IBKR Pro tiered `$0.0035/share, $0.35 min, max 1% of trade value`; modeled slippage = 50% of half-spread on marketable limit orders.

6. **No `MarketOrder` for entries.** Use `LimitOrder` (marketable limit) or peg-to-midpoint. `MarketOrder` is acceptable only in two sanctioned paths: (a) kill switch emergency liquidation, (b) end-of-day forced flatten before market close.

7. **State is durable.** Open position, pending orders, daily P&L, runtime configuration, detected S/R levels — all live in PostgreSQL. The bot must restart and reconcile its world view from disk + IBKR's view of the account. Memory-only state is forbidden for anything that matters.

8. **Logs are structured (JSON), never `print()`.** Use the project logger. Every order, fill, signal, S/R update, error, reconnection, config change must be logged with enough context to reconstruct what happened.

9. **Kill switch is sacred.** Any of: env var `KILL_SWITCH=true`, Telegram `/kill`, or the kill button in the web app must cause the bot to cancel all open orders, flatten the position at market, and refuse new signals until manually reset. This path must be tested.

10. **Secrets never in code, never in git.** `.env` is gitignored. Configuration loads via `pydantic-settings`. Hardcoded passwords, API keys, account numbers are P0 bugs.

11. **Runtime configuration lives in the database, not in YAML.** Risk limits, S/R parameters, profit range, the active asset for the day — all editable from the web app, persisted to Postgres, audit-logged on every change. YAML is only for bootstrap defaults on a fresh install.

12. **The web API is authenticated and TLS-only.** Exposing the dashboard to the internet without auth and HTTPS is a P0 bug.

---

## 3. Tech stack (fixed decisions)

| Layer | Choice | Rationale |
|---|---|---|
| Language (bot + API) | Python 3.11+ | `ib_insync` requires 3.11; async-native |
| Broker API | `ib_insync` over IB Gateway | De-facto standard for IBKR algo trading in Python |
| Gateway management | IBC (IBController) | Handles 2FA + daily forced restart |
| Database | PostgreSQL 15+ with TimescaleDB | Tick storage at scale; structured order/fill/config records |
| Cache / pub-sub | Redis 7+ | Live position cache, rate limiting, bot↔API pub/sub |
| Backend API | FastAPI + Uvicorn | Same language as bot; shares ORM models; async |
| Frontend | React 18 + Vite + TypeScript + TanStack Query + Tailwind | Standard SPA; polls API every 3–5 s |
| Reverse proxy / TLS | Caddy | Automatic HTTPS via Let's Encrypt |
| Edge (production) | Cloudflare free tier | WAF, DDoS protection, DNS |
| Auth | Cookie session + Argon2 password + TOTP (self-hosted) | Single operator; no SaaS dependency |
| Package management | `uv` | Fast, reproducible, lockfile-based |
| Config (bot + API) | `pydantic-settings` for env/secrets; Postgres for runtime knobs | Validated configs; live edit |
| Logging | `structlog` → JSON → file + stdout | Machine-readable; queryable |
| Monitoring | Grafana + Prometheus | Dashboards; native metrics |
| Alerting | Telegram bot | Push notifications + commands |
| Process supervision | `systemd` (Linux VPS) / `launchd` (Mac dev) | OS-native |
| Containerization | Docker Compose for stateful infra (Postgres, Redis, Grafana, Prometheus, Caddy) | Bot itself runs on host for lowest latency |
| Testing | `pytest` + `pytest-asyncio` + `hypothesis` | Property-based tests for risk logic |
| Lint / format / types | `ruff` + `mypy --strict` | Strict types; no exceptions |
| Historical data | **IBKR only** (`reqHistoricalData` + `keepUpToDate`) | Covers 1m/5m/15m bars with live streaming |
| Backtesting | **Deferred to post-v1** | Will be added once live operation is stable |
| Error tracking (optional) | Sentry free tier | Bot + API exceptions |
| Backups (optional) | Backblaze B2 | Daily encrypted Postgres dump |

**Do not propose alternatives to these without strong reason.** The owner has already evaluated and chosen.

---

## 4. Project structure

```
Trading_algoritmico/
├── CLAUDE.md                       # this file
├── PRD.md                          # product requirements
├── README.md                       # operator quickstart
├── pyproject.toml                  # uv / pip metadata
├── uv.lock
├── .env.example                    # template, real .env is gitignored
├── .gitignore
├── docker-compose.yml              # postgres + timescale + redis + grafana + prometheus + caddy
├── infra/
│   ├── ibc/
│   │   ├── config.ini.example
│   │   └── start_gateway.sh
│   ├── caddy/
│   │   └── Caddyfile
│   ├── grafana/dashboards/
│   ├── prometheus/prometheus.yml
│   └── systemd/tradingbot.service
├── config/
│   └── defaults.yaml               # bootstrap defaults; runtime config lives in DB
├── src/
│   ├── tradingbot/                 # the bot
│   │   ├── __init__.py
│   │   ├── main.py                 # bot entry point
│   │   ├── settings.py             # pydantic-settings (env + secrets)
│   │   ├── logging_setup.py
│   │   ├── connector/              # CAPA 1: IBKR connection
│   │   ├── data/                   # CAPA 2: market data, S/R detection
│   │   ├── execution/              # CAPA 3: order routing
│   │   ├── strategy/               # CAPA 4: position state machine + strategies
│   │   ├── risk/                   # CAPA 5: risk manager
│   │   ├── monitoring/             # CAPA 6: telegram, metrics, kill switch
│   │   └── persistence/            # CAPA 0: ORM models, migrations, repositories
│   └── tradingbot_api/             # FastAPI app
│       ├── __init__.py
│       ├── main.py                 # API entry point
│       ├── routes/
│       ├── schemas/                # pydantic request/response models
│       └── auth/
├── frontend/                       # React + Vite dashboard
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   └── src/
├── tests/
│   ├── unit/
│   ├── integration/                # require postgres + paper IBKR
│   └── property/                   # hypothesis-based tests for risk logic
└── scripts/
    ├── reconcile.py                # reconcile bot state vs IBKR
    ├── flatten_all.py              # emergency liquidation
    └── seed_config.py              # seed DB from config/defaults.yaml
```

**Rules:**
- Bot code under `src/tradingbot/`, API code under `src/tradingbot_api/`. Both share `tradingbot.persistence` (ORM, DB session). The API never imports from the bot's strategy/execution/risk modules.
- The frontend (`frontend/`) is a separate npm project. It only talks to the API.
- The bot and the API are **two separate processes**. They communicate exclusively through PostgreSQL + Redis pub/sub. Never through in-process imports.

---

## 5. Development phases

Strict phasing. **Do not skip ahead.** Each phase has exit criteria.

### Phase 0 — Prerequisites (manual, owner-driven)
- [ ] IBKR PDT clarification received in writing
- [ ] Account funded with target capital (~$150k USD)
- [ ] Account confirmed on IBKR Pro (not Lite)
- [ ] US market data subscription active (~$15/mo, required for real-time bars; without it data is delayed 15 min)
- [ ] Market Data API Acknowledgement signed
- [ ] Paper trading account created
- [ ] IB Gateway installed and tested manually
- [ ] IBC installed and auto-login working

**Exit criterion:** owner can run a one-liner that connects to paper Gateway and prints account balance + a live 1-min bar for a sample ticker.

### Phase 1 — Bot infrastructure (current focus)
- [ ] Repo initialized: `CLAUDE.md`, `PRD.md`, `pyproject.toml`, `.gitignore`, `.env.example`
- [ ] `uv` project setup, dependencies installed
- [ ] `docker-compose.yml` with Postgres+TimescaleDB+Redis+Grafana+Prometheus
- [ ] `pydantic-settings` config loader (env + secrets)
- [ ] `structlog` logging setup
- [ ] CAPA 0 (persistence): SQLAlchemy models + initial Alembic migration
- [ ] CAPA 1 (connector): connect, reconnect with exponential backoff, account state subscription, heartbeat
- [ ] Telegram bot scaffold: `/status`, `/positions`, `/pnl`, `/kill`
- [ ] Smoke test: bot connects to paper, logs account state every minute, survives gateway restart

**Exit criterion:** bot runs for 24 h connected to paper Gateway, survives a gateway restart, responds to Telegram commands. No strategies yet — just plumbing.

### Phase 2 — Web application
- [ ] FastAPI backend: auth (cookie + Argon2 + TOTP), config CRUD, dashboard read endpoints
- [ ] React + Vite + TypeScript frontend: login, dashboard, config screens, operations history
- [ ] Runtime configuration persisted to Postgres (`config_policies` table, audit-logged)
- [ ] Daily asset selection from the app
- [ ] Caddy reverse proxy + Cloudflare front (production)
- [ ] Dashboard polls API every 3–5 s
- [ ] Manual kill switch button (confirm-then-trip)

**Exit criterion:** operator logs into the web app from anywhere, sets the day's asset, edits risk parameters, sees (mock) operations history. No live bot integration yet beyond config read.

### Phase 3 — Strategy engine (paper)
- [ ] CAPA 2 (data): subscribe to 1m / 5m / 15m bars via `reqHistoricalData` + `keepUpToDate`, rolling 5-min window in memory
- [ ] S/R detector (see §6): scans bars, computes strength score, emits `SRLevel` records
- [ ] CAPA 4 (strategy): position state machine (CERRADA → ABRIENDO → ABIERTA → CERRANDO → CERRADA), discovery + management strategies
- [ ] CAPA 3 (execution): marketable-limit order router + bracket (stop + TP), end-of-day forced flatten
- [ ] CAPA 5 (risk): risk manager with daily loss cap, per-trade SL %, R-multiple filter, commission filter
- [ ] Strategy runs against paper account end-to-end
- [ ] All operations stream to the web dashboard

**Exit criterion:** bot executes the full discovery → entry → manage → exit cycle on paper for a full trading day, with operations visible in the web app. No crashes, no stuck states.

### Phase 4 — Forward testing (paper, ≥4 weeks)
- [ ] Bot runs paper for ≥4 consecutive weeks
- [ ] Daily review through the dashboard
- [ ] S/R formula parameters tuned based on observed performance
- [ ] No unexplained crashes or stuck states

**Exit criterion:** 4 consecutive weeks of clean paper operation with positive risk-adjusted results. **Calendar requirement, cannot be shortcut.**

### Phase 5 — Live, scaled gradually
- [ ] Move infra to NY VPS (latency)
- [ ] 2 weeks at minimum size (10–25 shares per trade)
- [ ] Compare live results to paper results daily
- [ ] Scale gating on results

---

## 6. Position state machine and strategy

The bot is governed by a **state machine** that determines which strategy runs at any moment.

### States

```
CERRADA   → discovery strategy is active. Scanning for entry.
ABRIENDO  → entry order open (limit, not yet filled). No new entry scanning.
ABIERTA   → management strategy active. Monitoring SL/TP. No new entries.
CERRANDO  → exit order open. Waiting for fill.
```

Transitions are durable: written to Postgres BEFORE the action that triggers them. Only one position in {ABRIENDO, ABIERTA, CERRANDO} at any time. This invariant is enforced by the risk manager.

### Discovery strategy (CERRADA state)

Runs continuously while CERRADA. Operates on a **60-minute lookback
window** (configurable: `sr_lookback_minutes = 60`).

**S/R detection.** Pivot extrema are computed independently on the
**5-minute** and **15-minute** bar series. A candidate level is kept
only when a 15-minute pivot is **confirmed** by a 5-minute pivot at
the same price (within `sr_level_tolerance_pct`). The 1-minute
series is NOT used for pivot detection — its job is to drive
trend-change signals (breakout vs rejection) in real time once a
level has been identified.

Indicators (session VWAP, EMA20, EMA50, EMA200, ATR(14)) are
computed on a longer rolling history of 1-minute bars
(`indicator_history_minutes = 240` at startup, then `keepUpToDate`
updates) so EMA200 has enough warm-up. The session VWAP reuses
IBKR's per-bar WAP field directly via `Σ(wap_i · vol_i) / Σ(vol_i)`
— IBKR exposes WAP per bar but no session-cumulative VWAP, EMA, or
ATR via the API (those are computed locally in
`src/tradingbot/data/indicators.py`).

For each kept candidate level `P`, compute a **strength score 0–100**:

| Component | Weight | Definition |
|---|---:|---|
| Clean touches | 35% | Distinct touches in `[P-tol, P+tol]` (default tol = 0.05%) that respected the level. Saturates at 5. |
| Volume at price | 30% | Volume profile: `vol_at_level / total_session_volume`, normalized to 0–100. |
| MA confluence | 20% | +25 per moving average within 0.1% of P. MAs: VWAP, EMA20, EMA50, EMA200. Max 100. |
| Persistence | 10% | Minutes the level has been respected without breaking. Saturates at 60. |
| Rejection quality | 5% | Average wick/body ratio of bars that touched the level. |

`strength = 0.35·C1 + 0.30·C2 + 0.20·C3 + 0.10·C4 + 0.05·C5`

**Entry decision:**
- `strength ≥ sr_strong_threshold` (default 70) → enter with **100%** of allocated position size.
- `sr_weak_threshold ≤ strength < sr_strong_threshold` (default 40–70) → enter with **50%**, reserve the other 50% for a second entry if price retests the same level and rebounds again.
- `strength < sr_weak_threshold` → skip.

**Direction:**
- Approach to resistance from below + rejection → SHORT.
- Approach to support from above + rebound → LONG.

**Position sizing (per trade):** sized so the **profit at target falls inside the configured profit range** ($100–$500 by default):
1. Identify the **target level** (next support for short, next resistance for long).
2. `target_pct = |target − entry| / entry`.
3. SL is fixed at `stop_loss_pct` (default 0.5%) on the opposite side.
4. `r_multiple = target_pct / stop_loss_pct`.
5. **Filter:** abort if `r_multiple < min_r_multiple` (default 1.5).
6. Compute size bounds:
   - `min_size_usd = min_profit_per_trade_usd / target_pct`
   - `max_size_usd = max_profit_per_trade_usd / target_pct`
7. `position_size = min(max_size_usd, max_position_size_usd)`. If `position_size < min_size_usd`, abort.
8. Estimate round-trip commission. **Filter:** abort if `commission / expected_profit > max_commission_pct_of_target` (default 5%).

All thresholds, weights, tolerances and bounds are editable from the web app and persisted in `config_policies`.

### Management strategy (ABIERTA state)

While position is open:
- Server-side **bracket orders** (stop-loss + take-profit) live on IBKR. They survive bot crashes.
- The bot monitors fills; on TP/SL fill, transitions to CERRANDO → CERRADA.
- Trailing stop logic is off by default for v1.

### End-of-day flatten

Independent of the state machine:
- `no_new_entries_before_close_minutes` (default 15): the bot stops scanning for entries N minutes before close.
- `force_flatten_before_close_minutes` (default 5): if still ABIERTA, submit `MarketOrder` to flatten. This is the only sanctioned use of `MarketOrder` outside the kill switch.

**Future strategies** (ORB, VWAP reclaim, Gap & Go, halt resumption) are out of scope for v1. Adding any of them requires updating this section first.

---

## 7. Risk limits (defaults — seeded from `config/defaults.yaml`, runtime values live in DB)

```yaml
# Capital and sizing
account_equity_target_usd:        150000
max_position_size_usd:            50000    # 33% of equity, hard cap

# Per-trade
stop_loss_pct:                    0.5      # of entry price
min_profit_per_trade_usd:         100
max_profit_per_trade_usd:         500
min_r_multiple:                   1.5      # target/risk minimum
max_commission_pct_of_target:     5.0      # skip if commissions > 5% of expected profit

# S/R thresholds
sr_strong_threshold:              70
sr_weak_threshold:                40
sr_partial_entry_pct:             50       # % of position on weak-signal first entry
sr_level_tolerance_pct:           0.05     # +/- band around a level for touch detection
sr_lookback_minutes:              60       # window for pivot detection on 5m + 15m
sr_pivot_window:                  1        # ± bars for local-extremum detection

# Trend-change signals (CERRADA, 1-minute series)
trend_change_lookback_minutes:    60       # 1m bars used to drive entries on a confirmed level

# Indicator warm-up (1-minute series)
indicator_history_minutes:        240      # rolling history retained for EMA200 / ATR

# Daily caps (circuit breaker triggers)
max_daily_loss_usd:               2250     # 1.5% of account → trip
max_trades_per_day:               50       # cap, not target
max_orders_per_minute:            30       # technical rate limit

# Market microstructure filters
min_spread_bps:                   0
max_spread_bps:                   20       # skip wider spreads
forbidden_tickers:                []
earnings_blackout:                true
halt_resume_cooldown_seconds:     60

# End-of-day
no_new_entries_before_close_minutes: 15
force_flatten_before_close_minutes:  5

# Circuit breaker
consecutive_losses_limit:         5
drawdown_pct_from_open:           1.5
```

The risk manager has **soft limits** (warn + log) and **hard limits** (refuse + kill). When in doubt, prefer hard. Hardcoded fallbacks in code must be at least as conservative as the values above.

---

## 8. Coding conventions

- **Python 3.11+** syntax — use `match`, `|` unions, `Self`, etc.
- **Strict typing** — `mypy --strict` must pass. No `Any` without justification in comments.
- **No bare `except`** — catch specific exceptions; log with traceback.
- **No `print()`** — use the `structlog` logger.
- **Async-first** — `asyncio` throughout; `ib_insync` is async-friendly.
- **Time:** all timestamps are timezone-aware UTC in code and storage. Display conversion (ET, Europe/Andorra) happens at the boundary.
- **Money:** use `decimal.Decimal` for prices and P&L, never `float`. DB columns are `NUMERIC`.
- **IDs:** orders have both an `ib_order_id` (assigned by IBKR) and an internal `order_id` (UUID). Never collapse them.
- **Naming:** functions `snake_case`, classes `PascalCase`, constants `UPPER_SNAKE`, private `_leading_underscore`.
- **Docstrings:** Google style. Public functions in `src/` must have docstrings.
- **Imports:** absolute imports only (`from tradingbot.risk import RiskManager`), never relative.

**Frontend conventions:**
- TypeScript `strict: true`. No `any` without explicit `// eslint-disable`.
- Functional components only. Hooks for state.
- TanStack Query for all server state. No Redux/Zustand unless justified.
- Tailwind utility classes for styling. No CSS-in-JS.
- API client generated from the FastAPI OpenAPI schema (`openapi-typescript`).

---

## 9. Testing conventions

- Every PR touching `src/tradingbot/risk/` must include unit tests AND property-based tests (Hypothesis). Risk code is where bugs cost real money.
- Every PR touching `src/tradingbot/execution/` must include integration tests against a mocked IB client.
- S/R detection: replay a known historical day and assert expected levels detected. Snapshot-style.
- Use `pytest -m "not integration"` for fast loop; `pytest -m integration` requires paper Gateway + Postgres + Redis up.
- Frontend: `vitest` for component logic; manual smoke for UI flows in v1 (e2e deferred).

---

## 10. Tasks Claude Code should refuse to do

Push back, don't proceed silently, if asked to:

- Hardcode credentials, account numbers, or API keys.
- Bypass the risk manager.
- Use `MarketOrder` outside the two sanctioned paths (kill switch, end-of-day flatten).
- Allow more than one open position at any time.
- Submit live orders from a test or script not in `src/tradingbot/main.py`'s execution path.
- Make the risk manager configurable to less restrictive values than the hardcoded fallbacks.
- Delete or overwrite Alembic migrations (always create a new one).
- Drop or alter `orders`, `fills`, `positions`, `pnl_daily`, `config_policies`, or `audit_log` outside a migration.
- Commit `.env`, `*.key`, `*.pem`, `infra/ibc/config.ini` (only `.example` versions).
- Add a new external paid service or subscription without flagging the cost first.
- Implement a strategy not documented in §6 or `PRD.md`.
- Expose the web API to the internet without authentication and TLS.
- Reduce modeled commissions or slippage in any cost calculation without owner approval.
- Modify the position state machine or the `single position` invariant without owner approval.

---

## 11. Communication style with the operator

The operator (Ruben) is technically literate (CEO, online sector background, comfortable with the stack) but not a professional quant. Explain trading concepts clearly, no jargon-for-jargon's-sake. **All code, comments, docstrings, logs, commit messages, PR descriptions, and the contents of `CLAUDE.md` / `PRD.md` / `README.md` are in English** for grep-ability and tool compatibility. Conversation with the operator in chat may be in Spanish.

When uncertain about a trading decision (strategy parameter, risk threshold, market microstructure assumption), **ask before coding**. A wrong default in this kind of system is worse than a delay.

---

## 12. Glossary

- **PDT:** Pattern Day Trader rule. FINRA rule requiring $25k min equity for accounts that do 4+ day trades in 5 business days. Always applies to this bot's operator.
- **NBBO:** National Best Bid and Offer.
- **VWAP:** Volume-Weighted Average Price.
- **ATR:** Average True Range — volatility measure.
- **LULD:** Limit-Up Limit-Down — exchange halt mechanism.
- **R / R-multiple:** unit of risk; 1R = the distance to the stop loss. A 2R win = profit equal to 2× the risk.
- **Slippage:** difference between expected fill price (e.g. midpoint at signal) and actual fill price.
- **Bracket order:** parent entry + child stop-loss + child take-profit, all submitted as one unit. IBKR supports this server-side.
- **IB Gateway:** lightweight version of IBKR's trading platform, for API-only use.
- **IBC:** IBKR Controller, third-party utility that auto-logs into Gateway and handles 2FA.
- **S/R:** Support / Resistance — price levels at which buying or selling pressure has historically been concentrated.

---

## 13. Where to look first when something goes wrong

1. **Bot won't start:** check `.env` exists and `pydantic-settings` validation passed; check Postgres+Redis are up (`docker compose ps`).
2. **Can't connect to IBKR:** check IB Gateway is running, port is correct (4002 paper / 4001 live), Trusted IPs include 127.0.0.1, Market Data API Acknowledgement signed.
3. **Orders not filling:** check Read-Only API toggle in Gateway is OFF; check `tif` (time-in-force) is sane; check market is open.
4. **No market data / delayed data:** check Market Data subscription is active, check the ticker is on a subscribed exchange.
5. **Bot crashed overnight:** Gateway force-restarts daily; IBC should have brought it back. Check `journalctl -u tradingbot` and IBC logs.
6. **Strategy not generating signals:** check the active asset is configured for today via the app; check `config_policies` has not zeroed-out thresholds; check the position state — if not CERRADA, the discovery strategy is intentionally silent.
7. **Web app can't reach the bot:** bot and API are separate processes. State flows through Postgres + Redis pub/sub. Check both are up and the Redis channels are subscribed.
8. **Two positions open simultaneously:** P0. Stop the bot, reconcile against IBKR, file an issue. The invariant is broken.
