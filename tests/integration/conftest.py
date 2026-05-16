"""Fixtures for integration tests.

These tests need a running Postgres reachable at
`INTEGRATION_DATABASE_URL` (defaults to the docker-compose Postgres on
localhost:5432). Run with:

    docker compose up -d postgres
    uv run alembic upgrade head
    INTEGRATION_DATABASE_URL=... uv run pytest -m integration

The session-scoped `engine` is reused across all tests for speed.
Each test gets a fresh empty database via TRUNCATE in the
function-scoped `session_factory`.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tradingbot.persistence import models  # noqa: F401  register models on metadata
from tradingbot.persistence.base import Base

DEFAULT_INTEGRATION_DB_URL = (
    "postgresql+asyncpg://tradingbot:tradingbot@localhost:5432/tradingbot"
)

# Tables to TRUNCATE between tests. Hypertables (bars, ticks) are
# omitted: tests in this file do not exercise them, and TRUNCATE on
# a hypertable would be slower for no benefit.
_TRUNCATE_TARGETS: tuple[str, ...] = (
    "fills",
    "orders",
    "signals",
    "positions",
    "sr_levels",
    "pnl_daily",
    "risk_events",
    "reconciliation_log",
    "audit_log",
    "config_policies",
    "active_asset_selections",
    "app_sessions",
    "users",
)


@pytest_asyncio.fixture(scope="session")
async def engine() -> AsyncIterator[AsyncEngine]:
    url = os.environ.get("INTEGRATION_DATABASE_URL", DEFAULT_INTEGRATION_DB_URL)
    eng = create_async_engine(url, pool_pre_ping=True)
    # Sanity-check the schema is in place; integration runs depend on
    # `alembic upgrade head` having been executed.
    async with eng.connect() as conn:
        await conn.execute(text("SELECT 1 FROM positions LIMIT 0"))
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session_factory(
    engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        for name in _TRUNCATE_TARGETS:
            await session.execute(text(f"TRUNCATE {name} RESTART IDENTITY CASCADE"))
        await session.commit()
    return factory


__all__ = ["Base", "engine", "session_factory"]
