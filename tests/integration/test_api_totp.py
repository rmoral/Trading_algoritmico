"""Integration tests for the TOTP enrollment + login flow."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pyotp
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
WEB_SECRET = "test-secret-32-bytes-long-_xyz_xxxx"


@pytest_asyncio.fixture
async def app_settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        web_admin_username=ADMIN_USERNAME,
        web_admin_password=SecretStr(ADMIN_PASSWORD),
        web_api_secret_key=SecretStr(WEB_SECRET),
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


async def test_login_without_totp_when_unenrolled(
    app: FastAPI,
) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post(
            "/api/auth/login",
            json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
        )
    assert r.status_code == 200


async def test_enroll_returns_secret_and_uri(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/me/totp/enroll")
    assert r.status_code == 200
    body = r.json()
    assert "secret" in body
    assert body["provisioning_uri"].startswith("otpauth://totp/")


async def test_verify_with_correct_code_persists_secret(
    client: httpx.AsyncClient,
) -> None:
    enroll = (await client.post("/api/me/totp/enroll")).json()
    secret = enroll["secret"]
    code = pyotp.TOTP(secret).now()

    r = await client.post(
        "/api/me/totp/verify", json={"secret": secret, "code": code}
    )
    assert r.status_code == 204

    # Now login WITHOUT totp_code should be rejected with 'totp_required'.
    # Logout first to drop the session cookie.
    await client.post("/api/auth/logout")
    r = await client.post(
        "/api/auth/login",
        json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
    )
    assert r.status_code == 401
    assert r.json()["detail"] == "totp_required"

    # Login WITH a current code succeeds.
    r = await client.post(
        "/api/auth/login",
        json={
            "username": ADMIN_USERNAME,
            "password": ADMIN_PASSWORD,
            "totp_code": pyotp.TOTP(secret).now(),
        },
    )
    assert r.status_code == 200


async def test_verify_with_wrong_code_rejected(client: httpx.AsyncClient) -> None:
    enroll = (await client.post("/api/me/totp/enroll")).json()
    r = await client.post(
        "/api/me/totp/verify",
        json={"secret": enroll["secret"], "code": "000000"},
    )
    assert r.status_code == 400


async def test_double_enroll_rejected(client: httpx.AsyncClient) -> None:
    enroll = (await client.post("/api/me/totp/enroll")).json()
    code = pyotp.TOTP(enroll["secret"]).now()
    await client.post(
        "/api/me/totp/verify", json={"secret": enroll["secret"], "code": code}
    )
    r = await client.post("/api/me/totp/enroll")
    assert r.status_code == 409


async def test_disenroll_clears_secret(client: httpx.AsyncClient) -> None:
    enroll = (await client.post("/api/me/totp/enroll")).json()
    secret = enroll["secret"]
    await client.post(
        "/api/me/totp/verify", json={"secret": secret, "code": pyotp.TOTP(secret).now()}
    )

    r = await client.request(
        "DELETE", "/api/me/totp", json={"code": pyotp.TOTP(secret).now()}
    )
    assert r.status_code == 204

    # Login without totp_code now works (no second factor enrolled).
    await client.post("/api/auth/logout")
    r = await client.post(
        "/api/auth/login",
        json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
    )
    assert r.status_code == 200


async def test_disenroll_requires_valid_code(client: httpx.AsyncClient) -> None:
    enroll = (await client.post("/api/me/totp/enroll")).json()
    secret = enroll["secret"]
    await client.post(
        "/api/me/totp/verify", json={"secret": secret, "code": pyotp.TOTP(secret).now()}
    )
    r = await client.request("DELETE", "/api/me/totp", json={"code": "000000"})
    assert r.status_code == 400


async def test_login_with_wrong_totp_rejected(client: httpx.AsyncClient) -> None:
    enroll = (await client.post("/api/me/totp/enroll")).json()
    secret = enroll["secret"]
    await client.post(
        "/api/me/totp/verify", json={"secret": secret, "code": pyotp.TOTP(secret).now()}
    )

    await client.post("/api/auth/logout")
    r = await client.post(
        "/api/auth/login",
        json={
            "username": ADMIN_USERNAME,
            "password": ADMIN_PASSWORD,
            "totp_code": "000000",
        },
    )
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid credentials"
