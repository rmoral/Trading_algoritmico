"""Tests for `build_bracket`."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tradingbot.execution import BracketSpec, build_bracket
from tradingbot.persistence.enums import OrderSide, OrderType, TimeInForce
from tradingbot.strategy.types import TradingSignal


def _signal(side: OrderSide) -> TradingSignal:
    if side == OrderSide.BUY:
        return TradingSignal(
            symbol="AAPL",
            side=side,
            entry_price=Decimal("100"),
            stop_loss_price=Decimal("99.5"),
            take_profit_price=Decimal("101"),
            qty=Decimal("250"),
            expected_profit_usd=Decimal("250"),
            expected_commission_usd=Decimal("3.50"),
            r_multiple=Decimal("2"),
            sr_level_id=None,
            sr_level_strength=Decimal("80"),
            is_partial=False,
        )
    return TradingSignal(
        symbol="AAPL",
        side=side,
        entry_price=Decimal("100"),
        stop_loss_price=Decimal("100.5"),
        take_profit_price=Decimal("99"),
        qty=Decimal("250"),
        expected_profit_usd=Decimal("250"),
        expected_commission_usd=Decimal("3.50"),
        r_multiple=Decimal("2"),
        sr_level_id=None,
        sr_level_strength=Decimal("80"),
        is_partial=False,
    )


def test_long_bracket_layout() -> None:
    bracket = build_bracket(_signal(OrderSide.BUY))
    assert isinstance(bracket, BracketSpec)

    # Entry: BUY LIMIT at entry + offset.
    assert bracket.entry.side == OrderSide.BUY
    assert bracket.entry.order_type == OrderType.LIMIT
    assert bracket.entry.qty == Decimal("250")
    assert bracket.entry.limit_price == Decimal("100.01")
    assert bracket.entry.time_in_force == TimeInForce.DAY

    # Stop-loss: SELL STOP at the SL price, GTC.
    assert bracket.stop_loss.side == OrderSide.SELL
    assert bracket.stop_loss.order_type == OrderType.STOP
    assert bracket.stop_loss.stop_price == Decimal("99.5")
    assert bracket.stop_loss.limit_price is None
    assert bracket.stop_loss.time_in_force == TimeInForce.GTC

    # Take-profit: SELL LIMIT at the TP price, GTC.
    assert bracket.take_profit.side == OrderSide.SELL
    assert bracket.take_profit.order_type == OrderType.LIMIT
    assert bracket.take_profit.limit_price == Decimal("101")
    assert bracket.take_profit.stop_price is None
    assert bracket.take_profit.time_in_force == TimeInForce.GTC


def test_short_bracket_inverts_sides() -> None:
    bracket = build_bracket(_signal(OrderSide.SELL))

    # Entry: SELL LIMIT at entry - offset.
    assert bracket.entry.side == OrderSide.SELL
    assert bracket.entry.limit_price == Decimal("99.99")

    # Both exits are BUY.
    assert bracket.stop_loss.side == OrderSide.BUY
    assert bracket.stop_loss.stop_price == Decimal("100.5")
    assert bracket.take_profit.side == OrderSide.BUY
    assert bracket.take_profit.limit_price == Decimal("99")


def test_negative_offset_rejected() -> None:
    with pytest.raises(ValueError):
        build_bracket(_signal(OrderSide.BUY), parent_offset=Decimal("-0.01"))


def test_zero_offset_keeps_entry_at_signal_price() -> None:
    bracket = build_bracket(_signal(OrderSide.BUY), parent_offset=Decimal("0"))
    assert bracket.entry.limit_price == Decimal("100")


def test_qty_propagates_to_every_leg() -> None:
    bracket = build_bracket(_signal(OrderSide.BUY))
    assert bracket.entry.qty == bracket.stop_loss.qty == bracket.take_profit.qty
