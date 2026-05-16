# Trading Algorítmico

Automated scalping bot for US equities via Interactive Brokers, controlled and monitored through a web application.

## Status

Phase 1 — bootstrap. Source of truth for project scope, conventions, and current state is `CLAUDE.md` (operational contract) and `PRD.md` (product requirements). Read both before contributing.

## Architecture (high level)

Two processes share Postgres + Redis pub/sub:

- **Bot** (`src/tradingbot/`): connects to IBKR, runs the position state machine (`CERRADA → ABRIENDO → ABIERTA → CERRANDO`), executes the discovery and management strategies. Headless.
- **Web API** (`src/tradingbot_api/`, Phase 2): FastAPI serving the React frontend (`frontend/`). Authenticated, HTTPS-only. The frontend talks only to the API; the API and the bot do not import each other.

## Quickstart (development)

Requirements: Python 3.11+, Docker + Docker Compose, [`uv`](https://github.com/astral-sh/uv).

```bash
# Install Python dependencies into a project-local virtualenv
uv sync

# Copy and edit environment
cp .env.example .env
# Fill in: IBKR paper credentials, Telegram bot token, secrets
# Generate WEB_API_SECRET_KEY with:
#   python -c "import secrets; print(secrets.token_urlsafe(32))"

# Start stateful infrastructure (Postgres + Redis + Grafana + Prometheus)
docker compose up -d

# Apply database schema (idempotent)
uv run alembic upgrade head

# Seed runtime configuration into `config_policies`
uv run python scripts/seed_config.py

# Run the bot (connects to paper IBKR, exposes /metrics on :9100,
# answers Telegram commands)
uv run tradingbot

# In a second terminal: run the web API (auth + dashboard endpoints).
# Requires WEB_API_SECRET_KEY and WEB_ADMIN_PASSWORD set in .env.
uv run tradingbot-api
```

The bot reads `.env` automatically. Live trading is gated by both
`LIVE_TRADING=true` AND `IBKR_PORT=4001` — any mismatch is rejected
at startup.

## Useful commands

```bash
# Tests, lint, types
uv run pytest tests/unit -q       # fast unit tests
uv run pytest -m integration      # tests requiring postgres + paper IBKR
uv run ruff check src tests       # lint
uv run mypy src tests             # types

# Schema management
uv run alembic upgrade head       # apply migrations
uv run alembic history            # show migration history
```

## Safety

This bot can submit real orders to a brokerage account. Before any change touching live trading, read:

- `CLAUDE.md` §2 — non-negotiable principles
- `CLAUDE.md` §10 — tasks Claude Code should refuse

Live trading is disabled by default. Enabling it requires both `LIVE_TRADING=true` and `IBKR_PORT=4001`.

## Documentation

- `CLAUDE.md` — operational contract, conventions, principles, current state.
- `PRD.md` — product requirements, vision, functional/non-functional spec.
- `docs/smoke-test.md` — manual end-to-end checklist to run before
  merging anything that touches the live execution path.
- `docs/production.md` — VPS + Caddy + Cloudflare deployment runbook.
