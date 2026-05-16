"""Integration tests for daily active-asset selection."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tradingbot.persistence.enums import PositionSide, PositionState
from tradingbot.persistence.models import (
    ActiveAssetSelection,
    AuditLog,
    Position,
)
from tradingbot.settings import Settings
from tradingbot_api.bootstrap import ensure_admin_user
from tradingbot_api.main import create_app

pytestmark = pytest.mark.integration

ADMIN_USERNAME = "test_admin"
ADMIN_PASSWORD = "ChangeMe-1234"


@pytest_asyncio.fixture
async def app_settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        web_admin_username=ADMIN_USERNAME,
        web_admin_password=SecretStr(ADMIN_PASSWORD),
        web_cookie_secure=False,
        web_session_lifetime_days=1,
    )


@pytest_asyncio.fixture
async def app(
    app_settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
) -> FastAPI:
    async with session_factory() as db:
        await ensure_admin_user(db, app_settings)
    app = create_app(settings=app_settings, session_factory=session_factory)
    app.state.settings = app_settings
    app.state.session_factory = session_factory
    return app


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        await c.post(
            "/api/auth/login",
            json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
        )
        yield c


async def test_get_when_unset_returns_404(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/active-asset")
    assert r.status_code == 404


async def test_put_requires_auth(app: FastAPI) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.put("/api/active-asset", json={"symbol": "AAPL"})
    assert r.status_code == 401


async def test_invalid_symbol_rejected(client: httpx.AsyncClient) -> None:
    for bad in ("", "aapl", "TOO_LONG_TICKER", "1AAPL", "AAPL!", " AAPL"):
        r = await client.put("/api/active-asset", json={"symbol": bad})
        assert r.status_code == 422, f"symbol={bad!r} should have been rejected"


async def test_set_and_get(client: httpx.AsyncClient) -> None:
    r = await client.put("/api/active-asset", json={"symbol": "AAPL"})
    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "AAPL"
    assert body["effective_to"] is None
    assert body["set_by"] == ADMIN_USERNAME

    r2 = await client.get("/api/active-asset")
    assert r2.status_code == 200
    assert r2.json()["symbol"] == "AAPL"


async def test_change_retires_previous_and_audits(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await client.put("/api/active-asset", json={"symbol": "AAPL"})
    r = await client.put("/api/active-asset", json={"symbol": "MSFT"})
    assert r.status_code == 200
    assert r.json()["symbol"] == "MSFT"

    async with session_factory() as db:
        rows = (
            await db.execute(
                select(ActiveAssetSelection).order_by(ActiveAssetSelection.effective_from)
            )
        ).scalars().all()
        assert len(rows) == 2
        assert rows[0].symbol == "AAPL"
        assert rows[0].effective_to is not None
        assert rows[1].symbol == "MSFT"
        assert rows[1].effective_to is None

        audits = (
            await db.execute(select(AuditLog).order_by(AuditLog.ts))
        ).scalars().all()
        actions = [a.action for a in audits]
        assert actions == ["active_asset_set", "active_asset_changed"]
        assert audits[1].before == {"symbol": "AAPL"}
        assert audits[1].after == {"symbol": "MSFT"}


async def test_change_blocked_when_position_open(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await client.put("/api/active-asset", json={"symbol": "AAPL"})
    async with session_factory() as db:
        db.add(
            Position(
                opened_at=datetime.now(UTC),
                symbol="AAPL",
                side=PositionSide.LONG,
                qty=Decimal("100"),
                avg_entry_price=Decimal("150.00"),
                state=PositionState.ABIERTA,
            )
        )
        await db.commit()

    r = await client.put("/api/active-asset", json={"symbol": "MSFT"})
    assert r.status_code == 409
    assert "cannot change active asset" in r.json()["detail"]

    # Confirm nothing changed in the DB.
    async with session_factory() as db:
        current = (
            await db.execute(
                select(ActiveAssetSelection).where(
                    ActiveAssetSelection.effective_to.is_(None)
                )
            )
        ).scalar_one()
        assert current.symbol == "AAPL"


async def test_dot_symbol_accepted(client: httpx.AsyncClient) -> None:
    """BRK.B and similar class-share tickers are valid."""
    r = await client.put("/api/active-asset", json={"symbol": "BRK.B"})
    assert r.status_code == 200
