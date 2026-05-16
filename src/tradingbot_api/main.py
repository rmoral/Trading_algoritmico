"""Web API entry point.

`create_app(settings, session_factory)` returns a fully wired FastAPI
application. The production path builds settings + factory via the
process environment; tests pass their own to keep the app pure.

A lifespan context bootstraps the admin user on startup and disposes
the engine on shutdown.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tradingbot.logging_setup import configure_logging, get_logger
from tradingbot.persistence.database import create_engine, create_session_factory
from tradingbot.settings import Settings, get_settings
from tradingbot_api.bootstrap import ensure_admin_user
from tradingbot_api.routes import (
    auth_router,
    health_router,
    me_router,
    status_router,
)


def create_app(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    engine: AsyncEngine | None = None,
) -> FastAPI:
    """Build the FastAPI app.

    `session_factory` and `engine` can be injected for tests. In
    production they are built from `settings` inside the lifespan.
    """
    settings_resolved = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        log = get_logger(__name__)
        if session_factory is not None:
            app.state.engine = engine
            app.state.session_factory = session_factory
        else:
            built_engine = create_engine(settings_resolved)
            built_factory = create_session_factory(built_engine)
            app.state.engine = built_engine
            app.state.session_factory = built_factory

        app.state.settings = settings_resolved
        log.info(
            "api_startup",
            host=settings_resolved.web_api_host,
            port=settings_resolved.web_api_port,
        )

        async with app.state.session_factory() as db:
            await ensure_admin_user(db, settings_resolved)

        yield

        log.info("api_shutdown")
        if session_factory is None and app.state.engine is not None:
            await app.state.engine.dispose()

    app = FastAPI(title="Trading bot API", version="0.1.0", lifespan=lifespan)
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(me_router)
    app.include_router(status_router)
    return app


# ASGI entry point: `uvicorn tradingbot_api.main:app`.
app: FastAPI = create_app()


def run() -> int:
    """Console-script entry point: `tradingbot-api`."""
    settings = get_settings()
    configure_logging(settings)
    uvicorn.run(
        "tradingbot_api.main:app",
        host=settings.web_api_host,
        port=settings.web_api_port,
        log_config=None,
    )
    return 0


if __name__ == "__main__":
    sys.exit(run())
