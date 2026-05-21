"""Project the active `config_policies` row onto typed runtime config.

The web app persists runtime configuration as versioned JSONB rows
in `config_policies` (see `tradingbot_api.config_service`). The bot
reads the current row — the one with `effective_to IS NULL` — and
builds the strongly-typed dataclasses each layer consumes:
`RiskLimits`, `SRDetectorConfig`, and `EngineConfig`.

`config/defaults.yaml` only bootstraps a fresh install through
`scripts/seed_config.py`; once seeded the database is the single
source of truth. Keys absent from the stored payload fall back to
the same defaults the seed used, so a payload written by an older
bot version still loads.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tradingbot.data.sr_detector import SRDetectorConfig
from tradingbot.data.sr_strength import DEFAULT_WEIGHTS, StrengthWeights
from tradingbot.persistence.models import ConfigPolicy
from tradingbot.risk.types import RiskLimits
from tradingbot.strategy.discovery import DiscoveryConfig
from tradingbot.strategy.engine import EngineConfig
from tradingbot.strategy.sizing import SizingConfig

# Discovery trigger tolerances are not yet web-editable; these are the
# defaults applied until they are promoted into `config_policies`. The
# loader still honours them if a future payload carries the keys.
DEFAULT_PROXIMITY_TOLERANCE_PCT: Decimal = Decimal("0.5")
DEFAULT_REJECTION_TOLERANCE_PCT: Decimal = Decimal("0.1")


class ConfigNotSeededError(RuntimeError):
    """Raised when `config_policies` has no current row to load."""


@dataclass(frozen=True)
class RuntimeConfig:
    """Everything the bot's trading loop needs from `config_policies`."""

    version: int
    risk_limits: RiskLimits
    sr_config: SRDetectorConfig
    engine_config: EngineConfig
    entry_limit_cancel_seconds: int
    config_reload_seconds: int
    force_flatten_before_close_minutes: int = 5


def _dec(payload: dict[str, Any], key: str, default: str | int | Decimal) -> Decimal:
    """Read a Decimal field; payloads store Decimals as JSON strings."""
    return Decimal(str(payload.get(key, default)))


def _int(payload: dict[str, Any], key: str, default: int) -> int:
    return int(payload.get(key, default))


def _build_weights(raw: dict[str, Any]) -> StrengthWeights:
    return StrengthWeights(
        clean_touches=Decimal(str(raw["clean_touches"])),
        volume_at_price=Decimal(str(raw["volume_at_price"])),
        ma_confluence=Decimal(str(raw["ma_confluence"])),
        persistence=Decimal(str(raw["persistence"])),
        rejection_quality=Decimal(str(raw["rejection_quality"])),
    )


def build_runtime_config(payload: dict[str, Any], version: int) -> RuntimeConfig:
    """Project a `config_policies.payload` dict onto the typed config.

    Pure: no I/O. `load_runtime_config` is the DB-backed entry point.
    """
    risk_limits = RiskLimits(
        max_position_size_usd=_dec(payload, "max_position_size_usd", 50000),
        stop_loss_pct=_dec(payload, "stop_loss_pct", "0.5"),
        min_r_multiple=_dec(payload, "min_r_multiple", "1.5"),
        max_commission_pct_of_target=_dec(
            payload, "max_commission_pct_of_target", "5.0"
        ),
        max_daily_loss_usd=_dec(payload, "max_daily_loss_usd", 2250),
        max_trades_per_day=_int(payload, "max_trades_per_day", 50),
        max_orders_per_minute=_int(payload, "max_orders_per_minute", 30),
        min_spread_bps=_int(payload, "min_spread_bps", 0),
        max_spread_bps=_int(payload, "max_spread_bps", 20),
        forbidden_tickers=tuple(payload.get("forbidden_tickers", [])),
        earnings_blackout=bool(payload.get("earnings_blackout", True)),
        halt_resume_cooldown_seconds=_int(
            payload, "halt_resume_cooldown_seconds", 60
        ),
        consecutive_losses_limit=_int(payload, "consecutive_losses_limit", 5),
        drawdown_pct_from_open=_dec(payload, "drawdown_pct_from_open", "1.5"),
    )

    sizing = SizingConfig(
        stop_loss_pct=risk_limits.stop_loss_pct,
        min_profit_per_trade_usd=_dec(payload, "min_profit_per_trade_usd", 100),
        max_profit_per_trade_usd=_dec(payload, "max_profit_per_trade_usd", 500),
        min_r_multiple=risk_limits.min_r_multiple,
        max_commission_pct_of_target=risk_limits.max_commission_pct_of_target,
        max_position_size_usd=risk_limits.max_position_size_usd,
    )

    discovery = DiscoveryConfig(
        strong_threshold=_dec(payload, "sr_strong_threshold", 70),
        weak_threshold=_dec(payload, "sr_weak_threshold", 40),
        partial_entry_pct=_dec(payload, "sr_partial_entry_pct", 50),
        proximity_tolerance_pct=_dec(
            payload, "proximity_tolerance_pct", DEFAULT_PROXIMITY_TOLERANCE_PCT
        ),
        rejection_tolerance_pct=_dec(
            payload, "rejection_tolerance_pct", DEFAULT_REJECTION_TOLERANCE_PCT
        ),
        sizing=sizing,
    )

    raw_weights = payload.get("sr_strength_weights")
    weights = (
        _build_weights(raw_weights)
        if isinstance(raw_weights, dict)
        else DEFAULT_WEIGHTS
    )

    sr_config = SRDetectorConfig(
        sr_lookback_minutes=_int(payload, "sr_lookback_minutes", 60),
        sr_pivot_window=_int(payload, "sr_pivot_window", 1),
        sr_level_tolerance_pct=_dec(payload, "sr_level_tolerance_pct", "0.05"),
        indicator_history_minutes=_int(payload, "indicator_history_minutes", 240),
        weights=weights,
    )

    no_new_entries_before_close_minutes = _int(
        payload, "no_new_entries_before_close_minutes", 15
    )
    engine_config = EngineConfig(
        discovery=discovery,
        trend_change_lookback_minutes=_int(
            payload, "trend_change_lookback_minutes", 60
        ),
        no_new_entries_before_close_minutes=no_new_entries_before_close_minutes,
    )

    return RuntimeConfig(
        version=version,
        risk_limits=risk_limits,
        sr_config=sr_config,
        engine_config=engine_config,
        entry_limit_cancel_seconds=_int(payload, "entry_limit_cancel_seconds", 5),
        config_reload_seconds=_int(payload, "config_reload_seconds", 30),
        force_flatten_before_close_minutes=_int(
            payload, "force_flatten_before_close_minutes", 5
        ),
    )


async def load_runtime_config(
    session_factory: async_sessionmaker[AsyncSession],
) -> RuntimeConfig:
    """Read the current `config_policies` row and build a `RuntimeConfig`.

    Raises `ConfigNotSeededError` when no policy exists yet — the
    operator must run `scripts/seed_config.py` first.
    """
    async with session_factory() as session:
        row = (
            await session.execute(
                select(ConfigPolicy).where(ConfigPolicy.effective_to.is_(None))
            )
        ).scalar_one_or_none()
    if row is None:
        raise ConfigNotSeededError(
            "config_policies has no current row; run "
            "`uv run python scripts/seed_config.py` before starting the bot."
        )
    return build_runtime_config(row.payload, row.version)


__all__ = [
    "ConfigNotSeededError",
    "RuntimeConfig",
    "build_runtime_config",
    "load_runtime_config",
]
