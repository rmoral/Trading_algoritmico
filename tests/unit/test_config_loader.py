"""Unit tests for the runtime config loader.

`build_runtime_config` is pure — exercised directly. `load_runtime_config`
is tested with a stubbed session factory so no database is required.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tradingbot.config_loader import (
    ConfigNotSeededError,
    build_runtime_config,
    load_runtime_config,
)
from tradingbot.data.sr_strength import DEFAULT_WEIGHTS


def _full_payload() -> dict[str, Any]:
    """A payload shaped like `config/defaults.yaml` after JSON round-trip."""
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
        "sr_lookback_minutes": 60,
        "sr_pivot_window": 1,
        "trend_change_lookback_minutes": 60,
        "indicator_history_minutes": 240,
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
        "forbidden_tickers": ["GME"],
        "earnings_blackout": True,
        "halt_resume_cooldown_seconds": 60,
        "consecutive_losses_limit": 5,
        "drawdown_pct_from_open": "1.5",
        "entry_limit_cancel_seconds": 5,
        "config_reload_seconds": 30,
    }


def test_build_runtime_config_full_payload() -> None:
    cfg = build_runtime_config(_full_payload(), version=7)

    assert cfg.version == 7
    assert cfg.entry_limit_cancel_seconds == 5
    assert cfg.config_reload_seconds == 30

    rl = cfg.risk_limits
    assert rl.max_position_size_usd == Decimal("50000")
    assert rl.stop_loss_pct == Decimal("0.5")
    assert rl.min_r_multiple == Decimal("1.5")
    assert rl.max_daily_loss_usd == Decimal("2250")
    assert rl.max_trades_per_day == 50
    assert rl.forbidden_tickers == ("GME",)
    assert rl.earnings_blackout is True
    assert rl.consecutive_losses_limit == 5

    sr = cfg.sr_config
    assert sr.sr_lookback_minutes == 60
    assert sr.sr_pivot_window == 1
    assert sr.sr_level_tolerance_pct == Decimal("0.05")
    assert sr.indicator_history_minutes == 240
    assert sr.weights.clean_touches == Decimal("0.35")
    assert sr.weights.rejection_quality == Decimal("0.05")

    eng = cfg.engine_config
    assert eng.trend_change_lookback_minutes == 60
    disc = eng.discovery
    assert disc.strong_threshold == Decimal("70")
    assert disc.weak_threshold == Decimal("40")
    assert disc.partial_entry_pct == Decimal("50")
    # Sizing mirrors the risk limits where they overlap.
    assert disc.sizing.min_profit_per_trade_usd == Decimal("100")
    assert disc.sizing.max_profit_per_trade_usd == Decimal("500")
    assert disc.sizing.max_position_size_usd == rl.max_position_size_usd


def test_build_runtime_config_falls_back_on_missing_keys() -> None:
    """An empty payload yields the seed defaults, never a crash."""
    cfg = build_runtime_config({}, version=1)

    assert cfg.risk_limits.max_trades_per_day == 50
    assert cfg.risk_limits.max_position_size_usd == Decimal("50000")
    assert cfg.risk_limits.forbidden_tickers == ()
    assert cfg.sr_config.weights == DEFAULT_WEIGHTS
    assert cfg.sr_config.sr_lookback_minutes == 60
    # Discovery tolerances are not web-editable yet -> module defaults.
    assert cfg.engine_config.discovery.proximity_tolerance_pct == Decimal("0.5")
    assert cfg.engine_config.discovery.rejection_tolerance_pct == Decimal("0.1")


def test_build_runtime_config_honours_payload_tolerances() -> None:
    """If a future payload carries the tolerances, the loader uses them."""
    payload = _full_payload()
    payload["proximity_tolerance_pct"] = "0.8"
    payload["rejection_tolerance_pct"] = "0.2"

    cfg = build_runtime_config(payload, version=2)

    assert cfg.engine_config.discovery.proximity_tolerance_pct == Decimal("0.8")
    assert cfg.engine_config.discovery.rejection_tolerance_pct == Decimal("0.2")


def _session_factory(row: object) -> async_sessionmaker[AsyncSession]:
    """Stub factory whose session yields `row` from `scalar_one_or_none`."""
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=row)

    session = MagicMock()
    session.execute = AsyncMock(return_value=result)

    class _Ctx:
        async def __aenter__(self) -> object:
            return session

        async def __aexit__(self, *args: object) -> None:
            return None

    return cast(
        "async_sessionmaker[AsyncSession]", MagicMock(side_effect=lambda: _Ctx())
    )


@pytest.mark.asyncio
async def test_load_runtime_config_raises_when_not_seeded() -> None:
    factory = _session_factory(row=None)
    with pytest.raises(ConfigNotSeededError):
        await load_runtime_config(factory)


@pytest.mark.asyncio
async def test_load_runtime_config_builds_from_row() -> None:
    row = MagicMock()
    row.payload = _full_payload()
    row.version = 4
    factory = _session_factory(row=row)

    cfg = await load_runtime_config(factory)

    assert cfg.version == 4
    assert cfg.risk_limits.max_daily_loss_usd == Decimal("2250")
