"""Web API (Phase 2).

FastAPI process that serves the React dashboard and exposes the
operator-facing endpoints (auth, status, config CRUD). Runs in a
separate process from the bot; the two communicate exclusively
through Postgres and Redis pub/sub. The API never imports from
`tradingbot.strategy`, `tradingbot.execution`, or `tradingbot.risk`.
"""

from tradingbot_api.main import create_app

__all__ = ["create_app"]
