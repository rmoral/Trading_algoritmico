"""Unit tests for `ConfigPolicyPayload`.

These pin the safety caps that the operator cannot relax from the
web app. Pair with `tests/unit/test_defaults_yaml.py` which validates
the shipped defaults.
"""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

import pytest
from pydantic import ValidationError

from tradingbot.risk.limits import (
    MAX_DAILY_LOSS_USD_CEILING,
    MAX_POSITION_SIZE_USD_CEILING,
    STOP_LOSS_PCT_CEILING,
)
from tradingbot_api.config_schema import ConfigPolicyPayload


def _baseline() -> dict[str, object]:
    """A known-good payload used as the starting point for negative tests."""
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


def test_baseline_validates() -> None:
    ConfigPolicyPayload.model_validate(_baseline())


def test_max_daily_loss_above_ceiling_rejected() -> None:
    p = _baseline()
    p["max_daily_loss_usd"] = str(MAX_DAILY_LOSS_USD_CEILING + Decimal("1"))
    with pytest.raises(ValidationError):
        ConfigPolicyPayload.model_validate(p)


def test_max_position_size_above_ceiling_rejected() -> None:
    p = _baseline()
    p["max_position_size_usd"] = str(MAX_POSITION_SIZE_USD_CEILING + Decimal("1"))
    with pytest.raises(ValidationError):
        ConfigPolicyPayload.model_validate(p)


def test_stop_loss_above_ceiling_rejected() -> None:
    p = _baseline()
    p["stop_loss_pct"] = str(STOP_LOSS_PCT_CEILING + Decimal("0.1"))
    with pytest.raises(ValidationError):
        ConfigPolicyPayload.model_validate(p)


def test_negative_stop_loss_rejected() -> None:
    p = _baseline()
    p["stop_loss_pct"] = "-0.5"
    with pytest.raises(ValidationError):
        ConfigPolicyPayload.model_validate(p)


def test_min_r_multiple_below_floor_rejected() -> None:
    p = _baseline()
    p["min_r_multiple"] = "0.9"
    with pytest.raises(ValidationError):
        ConfigPolicyPayload.model_validate(p)


def test_weak_threshold_must_be_below_strong() -> None:
    p = _baseline()
    p["sr_weak_threshold"] = 70  # equal to strong
    with pytest.raises(ValidationError):
        ConfigPolicyPayload.model_validate(p)


def test_min_profit_must_be_below_max_profit() -> None:
    p = _baseline()
    p["min_profit_per_trade_usd"] = "500"  # equal to max
    p["max_profit_per_trade_usd"] = "500"
    with pytest.raises(ValidationError):
        ConfigPolicyPayload.model_validate(p)


def test_min_spread_must_be_below_max_spread() -> None:
    p = _baseline()
    p["min_spread_bps"] = 20
    p["max_spread_bps"] = 20
    with pytest.raises(ValidationError):
        ConfigPolicyPayload.model_validate(p)


def test_flatten_must_precede_no_new_entries_in_time() -> None:
    """force_flatten_before_close_minutes < no_new_entries_before_close_minutes.

    Flatten happens AFTER we stop opening new entries. So flatten's
    minutes-before-close must be smaller (closer to the close).
    """
    p = _baseline()
    p["no_new_entries_before_close_minutes"] = 5
    p["force_flatten_before_close_minutes"] = 5  # equal: rejected
    with pytest.raises(ValidationError):
        ConfigPolicyPayload.model_validate(p)


def test_weights_must_sum_to_one() -> None:
    p = _baseline()
    p["sr_strength_weights"] = {
        "clean_touches": "0.50",
        "volume_at_price": "0.30",
        "ma_confluence": "0.20",
        "persistence": "0.10",
        "rejection_quality": "0.05",
    }  # sum = 1.15
    with pytest.raises(ValidationError):
        ConfigPolicyPayload.model_validate(p)


def test_unknown_field_rejected() -> None:
    p = _baseline()
    p["new_undocumented_knob"] = 42
    with pytest.raises(ValidationError):
        ConfigPolicyPayload.model_validate(p)


def test_missing_field_rejected() -> None:
    p = _baseline()
    del p["max_daily_loss_usd"]
    with pytest.raises(ValidationError):
        ConfigPolicyPayload.model_validate(p)


def test_baseline_is_deep_copyable() -> None:
    """Sanity that the dict can be passed by value to multiple validators."""
    a = _baseline()
    b = deepcopy(a)
    ConfigPolicyPayload.model_validate(a)
    ConfigPolicyPayload.model_validate(b)
