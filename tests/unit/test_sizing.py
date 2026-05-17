"""Tests for `size_trade` (CLAUDE.md §6 sizing algorithm).

Deterministic tests pin the eight-step math at concrete prices.
Hypothesis property tests assert the always-true invariants the
risk manager downstream relies on:
- The returned stop is on the opposite side of entry from the target.
- The returned take-profit equals the requested target.
- expected_profit_usd is positive when a result is returned.
- r_multiple >= min_r_multiple.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tradingbot.persistence.enums import OrderSide
from tradingbot.strategy import (
    SizingConfig,
    SizingRefusal,
    SizingResult,
    estimate_round_trip_commission,
    size_trade,
)


def _config(**overrides: object) -> SizingConfig:
    base: dict[str, object] = {
        "stop_loss_pct": Decimal("0.5"),
        "min_profit_per_trade_usd": Decimal("100"),
        "max_profit_per_trade_usd": Decimal("500"),
        "min_r_multiple": Decimal("1.5"),
        "max_commission_pct_of_target": Decimal("5"),
        "max_position_size_usd": Decimal("50000"),
    }
    base.update(overrides)
    return SizingConfig(**base)  # type: ignore[arg-type]


# ---------- happy paths ----------


def test_long_sizing_within_bounds() -> None:
    """Entry 100, target 101 -> 1% move, R = 2. Profit fits in [100, 500]."""
    result = size_trade(
        entry_price=Decimal("100"),
        target_price=Decimal("101"),
        side=OrderSide.BUY,
        config=_config(),
    )
    assert isinstance(result, SizingResult)
    # max_profit / target_pct(=1%) = 500 / 0.01 = 50_000 notional.
    # Capped at max_position_size_usd = 50_000. qty = 500.
    assert result.qty == Decimal("500")
    assert result.stop_loss_price == Decimal("99.5")  # 100 * (1 - 0.005)
    assert result.take_profit_price == Decimal("101")
    assert result.r_multiple == Decimal("2")
    # Profit = (101 - 100) * 500 = 500 USD.
    assert result.expected_profit_usd == Decimal("500")
    # Commission: max(500*0.0035, 0.35) = 1.75 per side -> 3.50 round trip.
    assert result.expected_commission_usd == Decimal("3.50")


def test_short_sizing_inverts_sides() -> None:
    result = size_trade(
        entry_price=Decimal("100"),
        target_price=Decimal("99"),  # short target below entry
        side=OrderSide.SELL,
        config=_config(),
    )
    assert isinstance(result, SizingResult)
    assert result.stop_loss_price == Decimal("100.5")  # SL ABOVE for shorts
    assert result.take_profit_price == Decimal("99")
    assert result.r_multiple == Decimal("2")


# ---------- refusals ----------


def test_long_target_below_entry_refused() -> None:
    result = size_trade(
        entry_price=Decimal("100"),
        target_price=Decimal("99"),
        side=OrderSide.BUY,
        config=_config(),
    )
    assert isinstance(result, SizingRefusal)


def test_short_target_above_entry_refused() -> None:
    result = size_trade(
        entry_price=Decimal("100"),
        target_price=Decimal("101"),
        side=OrderSide.SELL,
        config=_config(),
    )
    assert isinstance(result, SizingRefusal)


def test_r_multiple_too_low_refused() -> None:
    """Entry 100, target 100.5 -> 0.5% move = 1.0 R; below 1.5 min."""
    result = size_trade(
        entry_price=Decimal("100"),
        target_price=Decimal("100.5"),
        side=OrderSide.BUY,
        config=_config(min_r_multiple=Decimal("1.5")),
    )
    assert isinstance(result, SizingRefusal)
    assert "r_multiple" in result.reason


def test_position_size_below_min_refused() -> None:
    """Cap the notional cap below min_profit / target_pct => refuse."""
    result = size_trade(
        entry_price=Decimal("100"),
        target_price=Decimal("101"),  # 1%
        side=OrderSide.BUY,
        config=_config(max_position_size_usd=Decimal("5000")),  # 5000 < 10_000 min
    )
    # min_size = 100 / 0.01 = 10_000; max cap 5_000 -> refuse
    assert isinstance(result, SizingRefusal)
    assert "position_size_usd" in result.reason


def test_commission_too_high_refused() -> None:
    """A cheap stock with a small target -> commissions dominate."""
    result = size_trade(
        entry_price=Decimal("2"),  # cheap stock
        target_price=Decimal("2.02"),  # 1% move
        side=OrderSide.BUY,
        config=_config(),
    )
    # min_size = 100 / 0.01 = 10_000 -> 5000 shares at $2.
    # Commission per side = 5000 * 0.0035 = 17.5 -> round trip 35.
    # Profit = 0.02 * 5000 = 100. commission_pct = 35%. Refused.
    assert isinstance(result, SizingRefusal)
    assert "commission" in result.reason


def test_qty_below_one_refused() -> None:
    """Insanely high price with tiny notional bound -> qty rounds to 0."""
    result = size_trade(
        entry_price=Decimal("10000"),
        target_price=Decimal("10100"),  # 1%
        side=OrderSide.BUY,
        config=_config(
            min_profit_per_trade_usd=Decimal("1"),
            max_profit_per_trade_usd=Decimal("2"),
            max_position_size_usd=Decimal("9000"),  # below 1 share at 10k
        ),
    )
    assert isinstance(result, SizingRefusal)
    assert "qty" in result.reason


def test_zero_entry_price_refused() -> None:
    result = size_trade(
        entry_price=Decimal("0"),
        target_price=Decimal("1"),
        side=OrderSide.BUY,
        config=_config(),
    )
    assert isinstance(result, SizingRefusal)


def test_zero_stop_loss_pct_refused() -> None:
    result = size_trade(
        entry_price=Decimal("100"),
        target_price=Decimal("101"),
        side=OrderSide.BUY,
        config=_config(stop_loss_pct=Decimal("0")),
    )
    assert isinstance(result, SizingRefusal)


# ---------- commission helper ----------


def test_commission_uses_minimum() -> None:
    """Tiny order -> $0.35 minimum per side, $0.70 round trip."""
    assert estimate_round_trip_commission(Decimal("1")) == Decimal("0.70")


def test_commission_per_share_above_minimum() -> None:
    """1000 shares -> 1000 * 0.0035 = 3.50 per side, 7.00 round trip."""
    assert estimate_round_trip_commission(Decimal("1000")) == Decimal("7.0000")


# ---------- property tests ----------


@st.composite
def _long_inputs(
    draw: st.DrawFn,
) -> tuple[Decimal, Decimal, SizingConfig]:
    entry = draw(
        st.decimals(
            min_value=Decimal("20"),
            max_value=Decimal("500"),
            allow_nan=False,
            allow_infinity=False,
            places=2,
        )
    )
    move_pct = draw(
        st.decimals(
            min_value=Decimal("0.8"),
            max_value=Decimal("4"),
            allow_nan=False,
            allow_infinity=False,
            places=2,
        )
    )
    target = entry * (Decimal(1) + move_pct / Decimal(100))
    config = SizingConfig(
        stop_loss_pct=Decimal("0.5"),
        min_profit_per_trade_usd=Decimal("100"),
        max_profit_per_trade_usd=Decimal("500"),
        min_r_multiple=Decimal("1.5"),
        max_commission_pct_of_target=Decimal("5"),
        max_position_size_usd=Decimal("50000"),
    )
    return entry, target, config


@given(_long_inputs())
@settings(suppress_health_check=[HealthCheck.too_slow], deadline=None, max_examples=80)
def test_long_result_invariants(inputs: tuple[Decimal, Decimal, SizingConfig]) -> None:
    entry, target, config = inputs
    result = size_trade(
        entry_price=entry, target_price=target, side=OrderSide.BUY, config=config
    )
    if isinstance(result, SizingRefusal):
        return
    # Stop on the OPPOSITE side from target.
    assert result.stop_loss_price < entry < result.take_profit_price
    assert result.take_profit_price == target
    # R-multiple bound holds.
    assert result.r_multiple >= config.min_r_multiple
    # Profit is positive.
    assert result.expected_profit_usd > 0
    # Qty is a positive whole number.
    assert result.qty >= 1
    assert result.qty == result.qty.to_integral_value()
    # Commission stays within the cap.
    commission_pct = (
        result.expected_commission_usd / result.expected_profit_usd * Decimal(100)
    )
    assert commission_pct <= config.max_commission_pct_of_target


@given(_long_inputs())
@settings(suppress_health_check=[HealthCheck.too_slow], deadline=None, max_examples=80)
def test_short_result_invariants(inputs: tuple[Decimal, Decimal, SizingConfig]) -> None:
    """Mirror of the long invariants on the short side."""
    entry, _, config = inputs
    # Make a short target = entry * (1 - move_pct/100). We re-derive
    # from the long target so the move_pct is in the same range.
    move_pct = Decimal("1")
    target = entry * (Decimal(1) - move_pct / Decimal(100))
    result = size_trade(
        entry_price=entry, target_price=target, side=OrderSide.SELL, config=config
    )
    if isinstance(result, SizingRefusal):
        return
    assert result.take_profit_price < entry < result.stop_loss_price
    assert result.r_multiple >= config.min_r_multiple
    assert result.expected_profit_usd > 0


@pytest.mark.parametrize("qty", [Decimal("1"), Decimal("100"), Decimal("10000")])
def test_commission_is_monotone_in_qty(qty: Decimal) -> None:
    a = estimate_round_trip_commission(qty)
    b = estimate_round_trip_commission(qty + Decimal("1"))
    assert b >= a
