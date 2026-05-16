"""Integration tests for /api/status (with Redis state) and /api/kill."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import cast

import httpx
import pytest
import pytest_asyncio
from fakeredis import FakeAsyncRedis
from fastapi import FastAPI
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tradingbot.settings import Settings
from tradingbot.state import (
    CONNECTION_KEY,
    KILL_REQUEST_KEY,
    KILL_SWITCH_KEY,
    BotStatePublisher,
)
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
async def redis() -> AsyncIterator[Redis[bytes]]:
    r = cast("Redis[bytes]", FakeAsyncRedis())
    yield r
    await r.flushall()


@pytest_asyncio.fixture
async def app(
    app_settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    redis: Redis[bytes],
) -> FastAPI:
    async with session_factory() as db:
        await ensure_admin_user(db, app_settings)
    app = create_app(
        settings=app_settings, session_factory=session_factory, redis=redis
    )
    app.state.settings = app_settings
    app.state.session_factory = session_factory
    app.state.redis = redis
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


async def test_status_unknown_when_no_bot_state_published(
    client: httpx.AsyncClient,
) -> None:
    r = await client.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    assert body["connection_state"] == "unknown"
    assert body["kill_switch_tripped"] is False
    assert body["kill_switch_reason"] is None


async def test_status_reflects_published_connection_and_kill(
    client: httpx.AsyncClient,
    redis: Redis[bytes],
) -> None:
    pub = BotStatePublisher(redis)
    await pub.set_connection(connected=True)
    await pub.set_kill_switch(tripped=True, reason="telegram")

    r = await client.get("/api/status")
    body = r.json()
    assert body["connection_state"] == "connected"
    assert body["kill_switch_tripped"] is True
    assert body["kill_switch_reason"] == "telegram"


async def test_status_disconnected(client: httpx.AsyncClient, redis: Redis[bytes]) -> None:
    pub = BotStatePublisher(redis)
    await pub.set_connection(connected=False)
    r = await client.get("/api/status")
    assert r.json()["connection_state"] == "disconnected"


async def test_kill_requires_auth(app: FastAPI) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/api/kill", json={})
    assert r.status_code == 401


async def test_kill_writes_request_key_with_actor(
    client: httpx.AsyncClient,
    redis: Redis[bytes],
) -> None:
    r = await client.post("/api/kill", json={"reason": "Earnings tomorrow"})
    assert r.status_code == 202

    raw = await redis.get(KILL_REQUEST_KEY)
    assert raw is not None
    value = raw.decode() if isinstance(raw, bytes) else raw
    assert value.startswith(f"web:{ADMIN_USERNAME}")
    assert "Earnings tomorrow" in value


async def test_kill_idempotent_overwrites_existing_request(
    client: httpx.AsyncClient,
    redis: Redis[bytes],
) -> None:
    await client.post("/api/kill", json={"reason": "first"})
    r = await client.post("/api/kill", json={"reason": "second"})
    assert r.status_code == 202

    raw = await redis.get(KILL_REQUEST_KEY)
    assert raw is not None
    value = raw.decode() if isinstance(raw, bytes) else raw
    assert "second" in value
    assert "first" not in value


async def test_connection_state_expires_when_bot_silent(
    client: httpx.AsyncClient,
    redis: Redis[bytes],
) -> None:
    """If the bot stops refreshing, the API reports `unknown`."""
    await BotStatePublisher(redis).set_connection(connected=True)
    # Force-expire the key to simulate the bot going silent.
    await redis.delete(CONNECTION_KEY)
    r = await client.get("/api/status")
    assert r.json()["connection_state"] == "unknown"
    # The kill-switch key was never written, so it remains False:
    assert await redis.exists(KILL_SWITCH_KEY) == 0
