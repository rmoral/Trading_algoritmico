"""Async SQLAlchemy engine and session factory.

Both the bot and the API use this module to obtain a connection to
Postgres. Engines are cached per-process; tests can override with
`create_engine` / `create_session_factory` directly.
"""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tradingbot.settings import Settings, get_settings


def create_engine(settings: Settings) -> AsyncEngine:
    """Create a new async engine for the given settings.

    Use this directly in tests or when you need an engine with custom
    configuration. For normal application use, call `get_engine()`
    which caches a singleton.
    """
    return create_async_engine(
        settings.database_url,
        echo=False,
        pool_pre_ping=True,
        future=True,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create a session factory bound to the given engine."""
    return async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    """Return the singleton async engine for the current process."""
    return create_engine(get_settings())


@lru_cache(maxsize=1)
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the singleton async session factory."""
    return create_session_factory(get_engine())
