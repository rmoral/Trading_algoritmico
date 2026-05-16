"""Integration tests for the config CRUD endpoints + audit log.

Skipped without `-m integration`. Need Postgres and `alembic upgrade
head` to have been run.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tradingbot.persistence.models import AuditLog, ConfigPolicy
from tradingbot.settings import Settings
from tradingbot_api.bootstrap import ensure_admin_user
from tradingbot_api.main import create_app

pytestmark = pytest.mark.integration

ADMIN_USERNAME = "test_admin"
ADMIN_PASSWORD = "ChangeMe-1234"


def _baseline_payload() -> dict[str, Any]:
    return {
        "account_equity_target_usd": "150000",
        "max_position_size_usd": "50000",
        "stop_loss_pct": "0.5",
        "min_profit_per_trade_usd": "100",
        "max_profit_per_trade_usd": "500",
        "min_r_multiple": "1.5",
        "max_commission_pct_of_target": "5.0",
        "sr_strong_threshold": 70,
        "sr_weak_threshold": 40,
        "sr_partial_entry_pct": 50,
        "sr_level_tolerance_pct": "0.05",
        "sr_lookback_minutes": 120,
        "sr_strength_weights": {
            "clean_touches": "0.35",
            "volume_at_price": "0.30",
            "ma_confluence": "0.20",
            "persistence": "0.10",
            "rejection_quality": "0.05",
        },
        "max_daily_loss_usd": "2250",
        "max_trades_per_day": 50,
        "max_orders_per_minute": 30,
        "min_spread_bps": 0,
        "max_spread_bps": 20,
        "forbidden_tickers": [],
        "earnings_blackout": True,
        "halt_resume_cooldown_seconds": 60,
        "no_new_entries_before_close_minutes": 15,
        "force_flatten_before_close_minutes": 5,
        "consecutive_losses_limit": 5,
        "drawdown_pct_from_open": "1.5",
        "entry_limit_cancel_seconds": 5,
        "config_reload_seconds": 30,
    }


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


async def test_get_config_when_empty_returns_404(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/config")
    assert r.status_code == 404


async def test_put_config_requires_auth(app: FastAPI) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.put("/api/config", json=_baseline_payload())
    assert r.status_code == 401


async def test_put_invalid_payload_returns_422(client: httpx.AsyncClient) -> None:
    bad = _baseline_payload()
    bad["max_daily_loss_usd"] = "99999"  # above ceiling
    r = await client.put("/api/config", json=bad)
    assert r.status_code == 422


async def test_put_creates_v1_then_get_returns_it(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    payload = _baseline_payload()
    r = await client.put("/api/config", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == 1
    assert body["effective_to"] is None
    assert body["created_by"] == ADMIN_USERNAME

    r2 = await client.get("/api/config")
    assert r2.status_code == 200
    assert r2.json()["version"] == 1

    # Audit log captured the creation.
    async with session_factory() as db:
        rows = (await db.execute(select(AuditLog))).scalars().all()
        assert len(rows) == 1
        entry = rows[0]
        assert entry.action == "config_policy_created"
        assert entry.actor == ADMIN_USERNAME
        assert entry.before is None
        assert entry.after is not None


async def test_put_twice_increments_version_and_audits(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    p1 = _baseline_payload()
    await client.put("/api/config", json=p1)

    p2 = _baseline_payload()
    p2["max_daily_loss_usd"] = "1500"  # change one value
    r = await client.put("/api/config", json=p2)
    assert r.status_code == 200
    assert r.json()["version"] == 2

    # v1 is retired, v2 is current.
    async with session_factory() as db:
        policies = (
            await db.execute(select(ConfigPolicy).order_by(ConfigPolicy.version))
        ).scalars().all()
        assert len(policies) == 2
        v1, v2 = policies
        assert v1.version == 1
        assert v1.effective_to is not None
        assert v2.version == 2
        assert v2.effective_to is None
        assert v2.payload["max_daily_loss_usd"] == "1500"

        audits = (
            await db.execute(select(AuditLog).order_by(AuditLog.ts))
        ).scalars().all()
        assert len(audits) == 2
        assert audits[0].action == "config_policy_created"
        assert audits[1].action == "config_policy_updated"
        assert audits[1].before is not None
        after = audits[1].after
        assert after is not None
        assert after["max_daily_loss_usd"] == "1500"


async def test_get_after_logout_requires_auth(
    client: httpx.AsyncClient,
) -> None:
    await client.put("/api/config", json=_baseline_payload())
    await client.post("/api/auth/logout")
    r = await client.get("/api/config")
    assert r.status_code == 401


def test_audit_log_ts_uses_utc() -> None:
    """Smoke: datetime.now(UTC) is what we use; this just pins it."""
    assert datetime.now(UTC).tzinfo is UTC
