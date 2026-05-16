"""Integration tests for the web API auth flow.

Skipped without `-m integration`. Need Postgres and `alembic upgrade
head` to have been run. See `tests/integration/conftest.py`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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
        web_cookie_secure=False,  # over http in tests
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
    # Skip the lifespan when used through ASGITransport (it would
    # re-bootstrap and re-set app.state). Manually populate state.
    app.state.settings = app_settings
    app.state.session_factory = session_factory
    return app


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_healthz_does_not_require_auth(client: httpx.AsyncClient) -> None:
    r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_me_requires_auth(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/me")
    assert r.status_code == 401


async def test_login_rejects_wrong_password(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/api/auth/login",
        json={"username": ADMIN_USERNAME, "password": "wrong"},
    )
    assert r.status_code == 401


async def test_login_rejects_unknown_user(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/api/auth/login",
        json={"username": "nobody", "password": ADMIN_PASSWORD},
    )
    assert r.status_code == 401


async def test_login_then_me_then_logout(client: httpx.AsyncClient) -> None:
    # Login sets the session cookie.
    r = await client.post(
        "/api/auth/login",
        json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["username"] == ADMIN_USERNAME
    assert "tradingbot_session" in r.cookies

    # Subsequent /api/me succeeds with the session.
    r = await client.get("/api/me")
    assert r.status_code == 200
    assert r.json()["username"] == ADMIN_USERNAME

    # Logout clears the cookie and revokes the session server-side.
    r = await client.post("/api/auth/logout")
    assert r.status_code == 204

    # The original session is now revoked.
    r = await client.get("/api/me")
    assert r.status_code == 401


async def test_status_requires_auth(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/status")
    assert r.status_code == 401


async def test_status_returns_defaults_when_db_empty(
    client: httpx.AsyncClient,
) -> None:
    await client.post(
        "/api/auth/login",
        json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
    )
    r = await client.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    assert body["kill_switch_tripped"] is False
    assert body["open_position"] is None
    assert body["today_pnl"] is None
