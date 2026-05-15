# CLAUDE.md — Project Context for Claude Code

> This file is loaded automatically by Claude Code. It defines the project, its principles, conventions, and current state. **Read this completely before doing anything in this repo.**

---

## 1. What this project is

A fully-automated trading bot that executes **scalping strategies on US equities (NYSE / Nasdaq)** via the Interactive Brokers TWS API. Holding times are seconds to minutes. The system is designed to run unattended 24/5 during US market hours, with comprehensive risk controls, observability, and a hard separation between research (backtest) and production code paths.

**Owner:** Ruben Moral (single operator, CEO/founder context, ~25 years online sector experience, not a professional quant). Based in Andorra.

**This is NOT a research toy.** Real money will eventually flow through it. Code quality, testability, and risk controls matter more than features.

---

## 2. Critical principles — read before writing any code

These are non-negotiable. If a requested change violates one, push back and ask for clarification.

1. **Paper trading first, always.** No code path should be capable of submitting live orders unless an explicit `LIVE_TRADING=true` env var is set AND the configured IB Gateway port is the live port (4001), not paper (4002). Belt and suspenders.

2. **Risk manager is in the critical path.** Every order must pass through `risk.RiskManager.approve(order)` before submission. There is no "fast path" that bypasses it. If you find yourself wanting to bypass it for performance reasons, that is a design smell — fix the design.

3. **Strategy code never touches the broker directly.** Strategies emit `Signal` objects. The execution layer is what talks to IBKR. This separation is what makes backtesting meaningful.

4. **Backtest and live must share the same strategy code.** If you find yourself writing strategy logic that only works in one mode, stop. The strategy interface (`Strategy` ABC) is the contract. Backtester and live engine both feed it the same shape of data.

5. **Slippage and commissions are first-class citizens in backtests.** Any backtest that doesn't model them is a bug. Default assumptions: $0.0035/share commission with $0.35 minimum (IBKR Pro tiered), slippage = 50% of half-spread on market-style orders.

6. **No `MarketOrder` for scalping entries.** Use `LimitOrder` (peg-to-midpoint or marketable limit). `MarketOrder` is only acceptable for emergency liquidation in the kill switch path.

7. **State is durable.** Open positions, pending orders, daily P&L — all of it lives in PostgreSQL. The bot must be able to restart and reconcile its world view from disk + IBKR's view of the account. Memory-only state is forbidden for anything that matters.

8. **Logs are structured (JSON), never `print()`.** Use the project logger. Every order, fill, signal, error, reconnection event must be logged with enough context to reconstruct what happened.

9. **Kill switch is sacred.** A single env var (`KILL_SWITCH=true`) or a single Telegram command (`/kill`) must cause the bot to cancel all open orders, flatten all positions at market, and refuse to take new signals until manually reset. This path must be tested.

10. **Secrets never in code, never in git.** `.env` is gitignored. Configuration loads via `pydantic-settings`. If you see a hardcoded password, API key, or account number, that is a P0 bug.

---

## 3. Tech stack (fixed decisions)

| Layer | Choice | Rationale |
|---|---|---|
| Language | Python 3.11+ | `ib_insync` requires 3.11; async-native; scalping at seconds is fast enough |
| Broker API | `ib_insync` over IB Gateway | De-facto standard for IBKR algo trading in Python; async; clean |
| Gateway management | IBC (IBController) | Handles 2FA flow + daily forced restart |
| Database | PostgreSQL 15+ with TimescaleDB | Tick storage at scale; structured order/fill records |
| Cache / state | Redis 7+ | Live position state, rate limiting, pub/sub for monitoring |
| Backtest engine | `nautilus_trader` (primary) | Models microstructure realistically; the right tool for scalping |
| Historical tick data | Polygon.io Starter ($29/mo) | Tick-level US equities, decent coverage |
| Package management | `uv` | Fast, reproducible; lockfile-based |
| Config | `pydantic-settings` + `.env` + YAML | Validated configs, no surprises at runtime |
| Logging | `structlog` → JSON → file + stdout | Machine-readable; queryable |
| Monitoring | Grafana + Prometheus | Dashboards; native metrics |
| Alerting | Telegram bot | Already used by owner for OpenClaw; reuse pattern |
| Process supervision | `systemd` (Linux VPS) / `launchd` (Mac dev) | OS-native |
| Containerization | Docker Compose for stateful infra (Postgres, Redis, Grafana) | Bot itself runs on host for lowest latency |
| Testing | `pytest` + `pytest-asyncio` + `hypothesis` | Property-based tests for risk logic |
| Linting / formatting | `ruff` + `mypy --strict` | Strict types; no exceptions |
| CI | GitHub Actions | Run tests + lint on every push |

**Do not propose alternatives to these without strong reason.** The owner has already evaluated and chosen.

---

## 4. Project structure

```
tradingbot/
├── CLAUDE.md                       # this file
├── PRD.md                          # detailed product requirements
├── README.md                       # operator-facing quickstart
├── pyproject.toml                  # uv / pip metadata
├── uv.lock
├── .env.example                    # template, real .env is gitignored
├── .gitignore
├── docker-compose.yml              # postgres + timescale + redis + grafana + prometheus
├── infra/
│   ├── ibc/
│   │   ├── config.ini.example
│   │   └── start_gateway.sh
│   ├── grafana/
│   │   └── dashboards/
│   ├── prometheus/
│   │   └── prometheus.yml
│   └── systemd/
│       └── tradingbot.service
├── config/
│   ├── universe.yaml               # tickers being traded
│   ├── strategies/
│   │   ├── orb.yaml
│   │   ├── vwap_reclaim.yaml
│   │   ├── gap_go.yaml
│   │   └── halt_play.yaml
│   └── risk.yaml                   # hard risk limits
├── src/
│   └── tradingbot/
│       ├── __init__.py
│       ├── main.py                 # entry point for live bot
│       ├── settings.py             # pydantic-settings config loader
│       ├── connector/              # CAPA 1: IBKR connection
│       │   ├── ib_client.py
│       │   ├── reconnect.py
│       │   └── account_state.py
│       ├── data/                   # CAPA 2: market data
│       │   ├── subscriber.py
│       │   ├── bar_aggregator.py
│       │   ├── indicators.py       # VWAP, ATR, ranges
│       │   └── universe_manager.py
│       ├── execution/              # CAPA 3: order routing
│       │   ├── order_router.py
│       │   ├── order_state.py
│       │   └── slippage_tracker.py
│       ├── strategies/             # CAPA 4: strategies
│       │   ├── base.py             # Strategy ABC + Signal dataclass
│       │   ├── orb.py
│       │   ├── vwap_reclaim.py
│       │   ├── gap_go.py
│       │   └── halt_play.py
│       ├── risk/                   # CAPA 5: risk management
│       │   ├── risk_manager.py
│       │   ├── pre_trade_checks.py
│       │   └── circuit_breaker.py
│       ├── monitoring/             # CAPA 6: ops
│       │   ├── telegram_bot.py
│       │   ├── metrics.py          # prometheus
│       │   └── kill_switch.py
│       ├── persistence/            # CAPA 0: storage
│       │   ├── db.py
│       │   ├── models.py           # SQLAlchemy models
│       │   ├── migrations/         # alembic
│       │   └── repositories/
│       └── logging_setup.py
├── backtest/
│   ├── __init__.py
│   ├── engine.py                   # nautilus_trader integration
│   ├── data_loader.py              # polygon → timescale
│   ├── slippage_models.py
│   ├── commission_models.py
│   ├── reports/
│   └── notebooks/                  # jupyter for research only — never imported by src/
├── tests/
│   ├── unit/
│   ├── integration/                # requires postgres + paper IBKR
│   └── property/                   # hypothesis tests for risk logic
└── scripts/
    ├── reconcile.py                # reconcile bot state vs IBKR
    ├── flatten_all.py              # emergency liquidation
    └── seed_universe.py
```

**Rules for this layout:**
- Anything under `src/tradingbot/` is production code. Treat it as such.
- `backtest/notebooks/` is research only. Never import from `src/` into a notebook and never the other way around.
- `tests/` mirrors `src/` structure where it makes sense.

---

## 5. Development phases (current state)

The project is being built in strict phases. **Do not skip ahead.** Each phase has exit criteria; meet them before starting the next.

### Phase 0 — Prerequisites (manual, owner-driven)
- [ ] IBKR PDT clarification received in writing
- [ ] Account confirmed on IBKR Pro (not Lite)
- [ ] Market Data API Acknowledgement signed
- [ ] Paper trading account created
- [ ] IB Gateway installed and tested manually
- [ ] IBC installed and auto-login working

**Exit criterion:** owner can run a Python one-liner that connects to paper Gateway and prints account balance.

### Phase 1 — Infrastructure (current focus)
- [ ] Repo initialized with this CLAUDE.md, PRD.md, pyproject.toml, .gitignore
- [ ] `uv` project setup, dependencies installed
- [ ] `docker-compose.yml` with Postgres+TimescaleDB+Redis+Grafana+Prometheus
- [ ] `pydantic-settings` configuration loader
- [ ] `structlog` logging setup
- [ ] CAPA 1 (connector): connect, reconnect, account state subscription
- [ ] CAPA 0 (persistence): SQLAlchemy models + initial Alembic migration
- [ ] Telegram bot scaffold (commands: `/status`, `/positions`, `/pnl`, `/kill`)
- [ ] Smoke test: bot connects to paper, logs account state every minute, sends `/status` reply on Telegram

**Exit criterion:** bot can run for 24h connected to paper Gateway, survive a gateway restart, and respond to Telegram commands.

### Phase 2 — Historical data ingestion
- [ ] Polygon.io client wrapper
- [ ] Tick data ingestion → TimescaleDB hypertable
- [ ] At least 6 months of tick data for 50 liquid US tickers loaded
- [ ] Data quality checks (gaps, anomalies, splits, dividends)

**Exit criterion:** can run a query "give me all NBBO ticks for AAPL on 2025-09-15 between 14:30 and 15:00 UTC" and get sane data.

### Phase 3 — Backtest framework
- [ ] `nautilus_trader` integration
- [ ] Commission model matching IBKR Pro tiered
- [ ] Slippage model (half-spread × configurable factor)
- [ ] Strategy base class shared with live (CAPA 4 interface defined here)
- [ ] First strategy: Opening Range Breakout (ORB) implemented
- [ ] Backtest CLI: `tradingbot backtest --strategy orb --from 2025-01-01 --to 2025-09-30`
- [ ] Report generation: tearsheet with Sharpe, max DD, win rate, profit factor

**Exit criterion:** ORB backtested on 9 months data with realistic costs; out-of-sample results documented.

### Phase 4 — Live strategy execution (paper)
- [ ] CAPA 2 (data): real-time bar aggregation + indicators
- [ ] CAPA 3 (execution): order router with limit/marketable-limit/peg
- [ ] CAPA 5 (risk): risk manager with hard limits from `risk.yaml`
- [ ] Strategy engine runs ORB live against paper account
- [ ] Bracket orders (entry + stop + take-profit) working end-to-end
- [ ] Telegram alerts on every fill / SL / TP / error

**Exit criterion:** bot executes ORB on paper for a full trading day without crashes, intervention, or anomalies.

### Phase 5 — Forward testing (paper, 4–6 weeks minimum)
- [ ] Bot runs paper for ≥4 consecutive weeks
- [ ] Daily P&L matches backtest within tolerance
- [ ] No unexplained crashes or stuck states
- [ ] Operator review process established (weekly)

**Exit criterion:** 4 consecutive weeks of clean paper operation with results consistent with backtest. **This is a calendar requirement, not a code requirement. It cannot be shortcut.**

### Phase 6 — Live, scaled gradually
- [ ] Move infra to NY VPS (latency)
- [ ] 2 weeks at minimum size (10–25 shares per trade)
- [ ] Compare live results to paper results daily
- [ ] Scale tier 1 → 2 → 3 → full size, gating on results at each tier

**Exit criterion:** none — this is an ongoing operational phase.

---

## 6. Strategies — known catalog

Strategy names and behavioral specs. Implementations live in `src/tradingbot/strategies/`. Each must implement the `Strategy` interface from `base.py`.

### ORB (Opening Range Breakout) — first strategy to implement
- Define opening range = high/low of first 5 minutes after 09:30 ET.
- After 09:35 ET, place a buy-stop just above the range high and a sell-stop just below the range low (or use synthetic stops in code).
- Only triggers between 09:35 and 11:00 ET.
- Position sizing: risk fixed % of equity (default 0.25%) per trade; stop distance = range size.
- Stop loss: opposite side of opening range.
- Take profit: 2R (twice the risk) OR end-of-session, whichever first.
- Exclusions: tickers with earnings today (premarket or postmarket), tickers under $5, ADV < 1M shares, spreads > 5 bps at 09:30.

### VWAP reclaim/rejection (later phase)
- For trending tickers, pullback to VWAP + bounce = entry.
- Direction determined by 30-min trend filter.

### Gap & Go (later phase)
- Gap > 4% on positive volume premarket, breakout of premarket high after open.

### Halt resumption play (later phase)
- Detect LULD halts via IBKR; play the reopen momentum.

**For Claude Code:** when asked to implement a new strategy, refuse if its behavioral spec is not documented here or in `PRD.md`. Ask the owner to specify first.

---

## 7. Risk limits (defaults — concrete values in `config/risk.yaml`)

These are starting defaults. Risk manager loads them from YAML at startup. **Hardcoded fallbacks in code must be conservative** (i.e. tighter than YAML).

```yaml
risk:
  max_daily_loss_usd: 200          # paper: tight; will be revisited for live
  max_daily_loss_pct: 1.0          # of starting equity
  max_open_positions: 3            # concurrent
  max_position_size_pct: 5.0       # of equity
  max_position_size_usd: 2000      # absolute cap
  max_trades_per_day: 50
  max_orders_per_minute: 30        # rate limit guard
  min_spread_bps: 0                # min acceptable spread
  max_spread_bps: 20               # if spread wider, refuse to trade
  max_slippage_bps_per_trade: 15   # if measured slippage exceeds, alert
  forbidden_tickers: []            # explicit blocklist
  earnings_blackout: true          # refuse to trade on earnings day
  halt_resume_cooldown_seconds: 60 # if a halt resolves, wait this long
  circuit_breaker:
    consecutive_losses: 5          # after N consecutive losing trades → halt for the day
    drawdown_pct_from_open: 1.5    # if equity drops X% from session open → halt
```

The risk manager has both **soft limits** (warn + log) and **hard limits** (refuse + kill). When in doubt, prefer hard.

---

## 8. Coding conventions

- **Python 3.11+ syntax** — use `match`, `|` unions, `Self`, etc.
- **Strict typing** — `mypy --strict` must pass. No `Any` without justification in comments.
- **No bare `except`** — catch specific exceptions; log with traceback.
- **No `print()`** — use the `structlog` logger.
- **Async-first** — use `asyncio` throughout the live bot; `ib_insync` is async-friendly.
- **Time:** all timestamps are timezone-aware UTC in code and storage. Display conversion (ET, Europe/Andorra) happens at the boundary.
- **Money:** use `decimal.Decimal` for prices and P&L, never `float`. The DB columns are NUMERIC.
- **IDs:** orders have both an `ib_order_id` (assigned by IBKR) and an internal `order_id` (UUID). Never collapse them.
- **Naming:** functions `snake_case`, classes `PascalCase`, constants `UPPER_SNAKE`, private `_leading_underscore`.
- **Docstrings:** Google style. Public functions in `src/` must have docstrings.
- **Imports:** absolute imports only (`from tradingbot.risk import RiskManager`), never relative.

## 9. Testing conventions

- Every PR/change touching `src/tradingbot/risk/` must include unit tests AND property-based tests (Hypothesis). Risk code is where bugs cost real money.
- Every PR/change touching `src/tradingbot/execution/` must include integration tests against a mocked IB client.
- Strategy tests: replay a known historical day and assert expected signals/trades. Snapshot-style.
- Use `pytest -m "not integration"` for fast loop; `pytest -m integration` requires paper Gateway running.

## 10. Tasks Claude Code should refuse to do

Push back, don't proceed silently, if asked to:

- Hardcode credentials or account numbers.
- Bypass the risk manager "just for this test".
- Use `MarketOrder` for entries.
- Submit live orders from a test or script not in `src/tradingbot/main.py`'s execution path.
- Make backtest results look better by reducing modeled costs without owner approval.
- Delete or overwrite Alembic migrations (always create a new one).
- Drop or alter the `orders`, `fills`, or `pnl_daily` tables outside a migration.
- Commit `.env`, `*.key`, `*.pem`, `config.ini` (only `.example` versions).
- Add a new external service or paid subscription without flagging the cost first.

## 11. Communication style with the operator

The operator (Ruben) is technically literate (CEO, online sector background, comfortable with the stack) but is not a professional quant. When explaining trading concepts, default to clear, concise, no jargon-for-jargon's-sake. When explaining code, assume he reads it but may not have written Python full-time recently. Spanish is fine in human-facing messages, but **all code, comments, docstrings, logs, and commit messages are in English** for grep-ability and tool compatibility.

When uncertain about a trading decision (strategy parameter, risk threshold, market microstructure assumption), **ask before coding**. A wrong default in this kind of system is worse than a delay.

---

## 12. Glossary

- **PDT:** Pattern Day Trader rule. FINRA rule requiring $25k min equity for accounts that do 4+ day trades in 5 business days. Bot is designed assuming PDT applies until IBKR confirms otherwise.
- **NBBO:** National Best Bid and Offer.
- **VWAP:** Volume-Weighted Average Price.
- **ATR:** Average True Range — volatility measure.
- **LULD:** Limit-Up Limit-Down — exchange halt mechanism.
- **R / "R-multiple":** unit of risk; 1R = the distance to the stop loss. A 2R win = profit equal to 2× the risk.
- **Slippage:** difference between expected fill price (e.g. midpoint at signal) and actual fill price.
- **Bracket order:** parent entry + child stop-loss + child take-profit, all submitted as one unit. IBKR supports this server-side.
- **IB Gateway:** lightweight version of IBKR's trading platform, designed for API-only use.
- **IBC:** IBKR Controller, third-party utility that auto-logs into Gateway and handles 2FA.

---

## 13. Where to look first when something goes wrong

1. **Bot won't start:** check `.env` exists and `pydantic-settings` validation passed; check Postgres+Redis are up (`docker compose ps`).
2. **Can't connect to IBKR:** check IB Gateway is running, port is correct (4002 paper / 4001 live), Trusted IPs include 127.0.0.1, Market Data API Acknowledgement signed.
3. **Orders not filling:** check Read-Only API toggle in Gateway is OFF; check the order's `tif` (time-in-force) is sane; check market is open.
4. **No market data:** check Market Data subscription is active, check ticker is in subscribed exchanges, check you're not over the 100 lines limit.
5. **Bot crashed overnight:** Gateway force-restarts daily; IBC should have brought it back. Check `journalctl -u tradingbot` and IBC logs.
6. **Strategy not generating signals:** check `config/universe.yaml` has tickers, check strategy YAML config has not zeroed-out parameters.
