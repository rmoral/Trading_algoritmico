"""Pydantic schema for the runtime configuration payload.

Mirrors `config/defaults.yaml` field-for-field. Every numeric field
has bounds; safety-critical bounds reference `tradingbot.risk.limits`
so a single source of truth governs both this validator and the
risk manager's hardcoded fallbacks.

When a new tunable is added to the bot:
1. Add the field to `config/defaults.yaml` (bootstrap value).
2. Add the field here with explicit bounds.
3. If safety-critical, add a constant in `tradingbot.risk.limits`
   and reference it in the bound.
4. Add a test in `tests/unit/test_config_schema.py` pinning the bound.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tradingbot.risk.limits import (
    MAX_COMMISSION_PCT_OF_TARGET_CEILING,
    MAX_DAILY_LOSS_USD_CEILING,
    MAX_ORDERS_PER_MINUTE_CEILING,
    MAX_POSITION_SIZE_USD_CEILING,
    MAX_PROFIT_PER_TRADE_USD_CEILING,
    MAX_SPREAD_BPS_CEILING,
    MAX_TRADES_PER_DAY_CEILING,
    MIN_R_MULTIPLE_FLOOR,
    STOP_LOSS_PCT_CEILING,
)

_WEIGHT_SUM_TOLERANCE = Decimal("0.01")


class SRStrengthWeights(BaseModel):
    """Weights for the five components of the S/R strength score."""

    model_config = ConfigDict(extra="forbid")

    clean_touches: Annotated[Decimal, Field(ge=0, le=1)]
    volume_at_price: Annotated[Decimal, Field(ge=0, le=1)]
    ma_confluence: Annotated[Decimal, Field(ge=0, le=1)]
    persistence: Annotated[Decimal, Field(ge=0, le=1)]
    rejection_quality: Annotated[Decimal, Field(ge=0, le=1)]

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> Self:
        total = (
            self.clean_touches
            + self.volume_at_price
            + self.ma_confluence
            + self.persistence
            + self.rejection_quality
        )
        if abs(total - Decimal("1")) > _WEIGHT_SUM_TOLERANCE:
            raise ValueError(f"weights must sum to 1.0 within 0.01 (got {total})")
        return self


class ConfigPolicyPayload(BaseModel):
    """Full runtime configuration the operator can edit from the web app.

    `extra="forbid"` rejects unknown keys so a typo in the UI cannot
    silently drop a field.
    """

    model_config = ConfigDict(extra="forbid")

    # Capital and sizing
    account_equity_target_usd: Annotated[Decimal, Field(gt=0)]
    max_position_size_usd: Annotated[
        Decimal, Field(gt=0, le=MAX_POSITION_SIZE_USD_CEILING)
    ]

    # Per-trade
    stop_loss_pct: Annotated[Decimal, Field(gt=0, le=STOP_LOSS_PCT_CEILING)]
    min_profit_per_trade_usd: Annotated[Decimal, Field(gt=0)]
    max_profit_per_trade_usd: Annotated[
        Decimal, Field(gt=0, le=MAX_PROFIT_PER_TRADE_USD_CEILING)
    ]
    min_r_multiple: Annotated[Decimal, Field(ge=MIN_R_MULTIPLE_FLOOR)]
    max_commission_pct_of_target: Annotated[
        Decimal, Field(gt=0, le=MAX_COMMISSION_PCT_OF_TARGET_CEILING)
    ]

    # S/R thresholds
    sr_strong_threshold: Annotated[int, Field(ge=0, le=100)]
    sr_weak_threshold: Annotated[int, Field(ge=0, le=100)]
    sr_partial_entry_pct: Annotated[int, Field(gt=0, lt=100)]
    sr_level_tolerance_pct: Annotated[Decimal, Field(gt=0, le=1)]
    sr_lookback_minutes: Annotated[int, Field(gt=0)]

    sr_strength_weights: SRStrengthWeights

    # Daily caps
    max_daily_loss_usd: Annotated[
        Decimal, Field(gt=0, le=MAX_DAILY_LOSS_USD_CEILING)
    ]
    max_trades_per_day: Annotated[int, Field(gt=0, le=MAX_TRADES_PER_DAY_CEILING)]
    max_orders_per_minute: Annotated[
        int, Field(gt=0, le=MAX_ORDERS_PER_MINUTE_CEILING)
    ]

    # Microstructure
    min_spread_bps: Annotated[int, Field(ge=0)]
    max_spread_bps: Annotated[int, Field(gt=0, le=MAX_SPREAD_BPS_CEILING)]
    forbidden_tickers: list[str] = Field(default_factory=list)
    earnings_blackout: bool
    halt_resume_cooldown_seconds: Annotated[int, Field(ge=0)]

    # End-of-day
    no_new_entries_before_close_minutes: Annotated[int, Field(ge=0)]
    force_flatten_before_close_minutes: Annotated[int, Field(ge=0)]

    # Circuit breaker
    consecutive_losses_limit: Annotated[int, Field(gt=0)]
    drawdown_pct_from_open: Annotated[Decimal, Field(gt=0)]

    # Order timing + config reload
    entry_limit_cancel_seconds: Annotated[int, Field(gt=0)]
    config_reload_seconds: Annotated[int, Field(gt=0)]

    @model_validator(mode="after")
    def _weak_below_strong(self) -> Self:
        if self.sr_weak_threshold >= self.sr_strong_threshold:
            raise ValueError(
                f"sr_weak_threshold ({self.sr_weak_threshold}) must be < "
                f"sr_strong_threshold ({self.sr_strong_threshold})"
            )
        return self

    @model_validator(mode="after")
    def _min_below_max_profit(self) -> Self:
        if self.min_profit_per_trade_usd >= self.max_profit_per_trade_usd:
            raise ValueError(
                f"min_profit_per_trade_usd ({self.min_profit_per_trade_usd}) "
                f"must be < max_profit_per_trade_usd "
                f"({self.max_profit_per_trade_usd})"
            )
        return self

    @model_validator(mode="after")
    def _min_below_max_spread(self) -> Self:
        if self.min_spread_bps >= self.max_spread_bps:
            raise ValueError(
                f"min_spread_bps ({self.min_spread_bps}) must be < "
                f"max_spread_bps ({self.max_spread_bps})"
            )
        return self

    @model_validator(mode="after")
    def _force_flatten_before_no_new_entries(self) -> Self:
        if (
            self.force_flatten_before_close_minutes
            >= self.no_new_entries_before_close_minutes
        ):
            raise ValueError(
                "force_flatten_before_close_minutes must be < "
                "no_new_entries_before_close_minutes (forced flatten happens "
                "AFTER we stop scanning for entries)"
            )
        return self
