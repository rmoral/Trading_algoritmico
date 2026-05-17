"""Pure helper: convert a `TradingSignal` into a 3-leg bracket spec.

A bracket order on IBKR is:
- parent: marketable LIMIT at the entry price (with a small offset),
- child A: STOP loss on the opposite side, attached to the parent,
- child B: take-profit LIMIT on the target side, attached to the
  parent, in an OCA group with the stop.

This module returns plain dataclasses. The `OrderRouter` translates
those into `ib_insync` order objects and submits them.

Keeping the conversion pure means it is trivially testable and the
risk manager can be re-run against `BracketSpec.entry` without
touching IBKR.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from tradingbot.persistence.enums import OrderSide, OrderType, TimeInForce
from tradingbot.strategy.types import TradingSignal

# IBKR Pro: marketable limits work best a few ticks beyond the
# midpoint. For US equities the default tick is $0.01; offsetting
# the parent by one tick lets it fill against the visible quote in
# normal conditions.
DEFAULT_PARENT_OFFSET: Decimal = Decimal("0.01")


@dataclass(frozen=True)
class EntryLegSpec:
    """Parent leg: marketable limit at signal entry +/- offset."""

    symbol: str
    side: OrderSide
    qty: Decimal
    order_type: OrderType
    limit_price: Decimal
    time_in_force: TimeInForce = TimeInForce.DAY


@dataclass(frozen=True)
class ExitLegSpec:
    """Child leg: either the stop-loss STOP or the take-profit LIMIT."""

    symbol: str
    side: OrderSide  # opposite of the entry side
    qty: Decimal
    order_type: OrderType  # STOP or LIMIT
    stop_price: Decimal | None
    limit_price: Decimal | None
    time_in_force: TimeInForce = TimeInForce.GTC


@dataclass(frozen=True)
class BracketSpec:
    """A complete bracket: entry + stop-loss child + take-profit child."""

    entry: EntryLegSpec
    stop_loss: ExitLegSpec
    take_profit: ExitLegSpec


def _opposite(side: OrderSide) -> OrderSide:
    return OrderSide.SELL if side == OrderSide.BUY else OrderSide.BUY


def build_bracket(
    signal: TradingSignal, *, parent_offset: Decimal = DEFAULT_PARENT_OFFSET
) -> BracketSpec:
    """Translate a `TradingSignal` into three concrete leg specs.

    The parent limit price is the signal's entry plus a small offset
    toward the direction we want to fill — for a long, slightly
    above the signal price; for a short, slightly below — so that
    the order is reliably marketable in normal book conditions.

    The exits use the prices the discovery strategy already computed
    and the risk manager approved.
    """
    if parent_offset < 0:
        raise ValueError(f"parent_offset must be >= 0; got {parent_offset}")

    if signal.side == OrderSide.BUY:
        parent_price = signal.entry_price + parent_offset
    else:
        parent_price = signal.entry_price - parent_offset

    exit_side = _opposite(signal.side)

    return BracketSpec(
        entry=EntryLegSpec(
            symbol=signal.symbol,
            side=signal.side,
            qty=signal.qty,
            order_type=OrderType.LIMIT,
            limit_price=parent_price,
        ),
        stop_loss=ExitLegSpec(
            symbol=signal.symbol,
            side=exit_side,
            qty=signal.qty,
            order_type=OrderType.STOP,
            stop_price=signal.stop_loss_price,
            limit_price=None,
        ),
        take_profit=ExitLegSpec(
            symbol=signal.symbol,
            side=exit_side,
            qty=signal.qty,
            order_type=OrderType.LIMIT,
            stop_price=None,
            limit_price=signal.take_profit_price,
        ),
    )
