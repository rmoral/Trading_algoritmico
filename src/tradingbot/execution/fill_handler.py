"""`FillHandler`: turn broker executions into durable position state.

IBKR pushes an execution + commission report for every fill and an
order-status update for every cancellation. This module consumes
those as plain domain events (`BrokerFill`, `BrokerOrderStatus`) and
drives the position state machine in CLAUDE.md §6:

```
ABRIENDO --entry fully filled--> ABIERTA
ABRIENDO --entry cancelled-----> CERRADA
ABIERTA  --exit starts filling-> CERRANDO
CERRANDO --exit fully filled---> CERRADA
```

The `ib_insync`-facing translation lives in `ibkr_fill_stream.py`;
this handler is deliberately broker-agnostic and unit-testable with
fakes.

Accounting model. A position has exactly one entry order and one
exit order (the bracket's stop and take-profit share an OCA group,
so only one of them executes; the end-of-day flatten is used only
when neither has). Per-order fill summaries therefore equal
position-level quantities, and `gross_pnl` is computed from the two
volume-weighted average prices.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from tradingbot.logging_setup import get_logger
from tradingbot.persistence.enums import (
    OrderSide,
    OrderStatus,
    PositionSide,
    PositionState,
)
from tradingbot.persistence.models import Order, Position
from tradingbot.persistence.repositories import (
    FillRepository,
    OrderRepository,
    PnLRepository,
    PositionsRepository,
)
from tradingbot.strategy.state_machine import InvalidTransitionError

_Clock = Callable[[], datetime]

DEFAULT_LOOKUP_ATTEMPTS: int = 5
DEFAULT_LOOKUP_DELAY_SECONDS: float = 0.2

# Order statuses that terminate an order without a full fill.
_CANCELLED_STATES: frozenset[OrderStatus] = frozenset(
    {OrderStatus.CANCELLED, OrderStatus.REJECTED}
)


@dataclass(frozen=True)
class BrokerFill:
    """One execution against one of our orders.

    `commission` is the commission for THIS execution (IBKR reports
    it per execution); the handler sums them per order.
    """

    ib_order_id: int
    exec_id: str
    ts: datetime
    qty: Decimal
    price: Decimal
    commission: Decimal
    exchange: str | None


@dataclass(frozen=True)
class BrokerOrderStatus:
    """A non-fill status transition for one of our orders."""

    ib_order_id: int
    status: OrderStatus


def _entry_side(position_side: PositionSide) -> OrderSide:
    """The order side that OPENS a position of the given direction."""
    return OrderSide.BUY if position_side is PositionSide.LONG else OrderSide.SELL


def _gross_pnl(
    position_side: PositionSide,
    entry_price: Decimal,
    exit_price: Decimal,
    qty: Decimal,
) -> Decimal:
    """Price-move P&L before commissions."""
    if position_side is PositionSide.LONG:
        return (exit_price - entry_price) * qty
    return (entry_price - exit_price) * qty


class FillHandler:
    """Apply broker executions and cancellations to durable state."""

    def __init__(
        self,
        *,
        positions_repo: PositionsRepository,
        order_repo: OrderRepository,
        fill_repo: FillRepository,
        pnl_repo: PnLRepository,
        clock: _Clock = lambda: datetime.now(UTC),
        lookup_attempts: int = DEFAULT_LOOKUP_ATTEMPTS,
        lookup_delay_seconds: float = DEFAULT_LOOKUP_DELAY_SECONDS,
    ) -> None:
        self._positions = positions_repo
        self._orders = order_repo
        self._fills = fill_repo
        self._pnl = pnl_repo
        self._clock = clock
        self._lookup_attempts = lookup_attempts
        self._lookup_delay = lookup_delay_seconds
        # Executions and cancellations both read-modify-write the open
        # position; serialise them so concurrent broker callbacks
        # cannot interleave a transition.
        self._lock = asyncio.Lock()
        self._log = get_logger(__name__)

    async def on_fill(self, fill: BrokerFill) -> None:
        """Record an execution and advance the position state machine."""
        async with self._lock:
            await self._on_fill_locked(fill)

    async def on_order_status(self, status: BrokerOrderStatus) -> None:
        """Record a cancellation/rejection and release a stuck open slot."""
        async with self._lock:
            await self._on_order_status_locked(status)

    async def _on_fill_locked(self, fill: BrokerFill) -> None:
        order = await self._resolve_order(fill.ib_order_id)
        if order is None:
            self._log.warning(
                "fill_handler_unknown_order", ib_order_id=fill.ib_order_id
            )
            return

        inserted = await self._fills.record_fill(
            order_id=order.id,
            ts=fill.ts,
            qty=fill.qty,
            price=fill.price,
            commission=fill.commission,
            exchange=fill.exchange,
            exec_id=fill.exec_id,
        )
        if not inserted:
            self._log.debug("fill_handler_duplicate_exec", exec_id=fill.exec_id)
            return

        total_qty, avg_price, commission = await self._fills.order_fill_summary(
            order.id
        )
        fully_filled = total_qty >= order.qty
        await self._orders.set_status(
            order.id,
            OrderStatus.FILLED if fully_filled else OrderStatus.PARTIAL_FILLED,
            ts_filled=fill.ts if fully_filled else None,
        )

        position = await self._positions.get_open_position()
        if position is None:
            self._log.warning(
                "fill_handler_no_open_position",
                ib_order_id=fill.ib_order_id,
                symbol=order.symbol,
            )
            return

        if self._is_entry(order, position):
            await self._apply_entry_fill(
                order, position, total_qty, avg_price, commission, fully_filled
            )
        else:
            await self._apply_exit_fill(
                order, position, total_qty, avg_price, commission, fill.ts,
                fully_filled,
            )

    async def _apply_entry_fill(
        self,
        order: Order,
        position: Position,
        total_qty: Decimal,
        avg_price: Decimal,
        commission: Decimal,
        fully_filled: bool,
    ) -> None:
        if not fully_filled:
            self._log.info(
                "fill_handler_entry_partial",
                symbol=order.symbol,
                filled_qty=str(total_qty),
                target_qty=str(order.qty),
            )
            return
        try:
            await self._positions.mark_open(
                position.id,
                avg_entry_price=avg_price,
                qty=total_qty,
                commissions=commission,
            )
        except InvalidTransitionError as exc:
            self._log.error(
                "fill_handler_entry_bad_state",
                position_id=str(position.id),
                state=position.state,
                error=str(exc),
            )
            return
        self._log.info(
            "fill_handler_position_opened",
            position_id=str(position.id),
            symbol=order.symbol,
            avg_entry_price=str(avg_price),
            qty=str(total_qty),
        )

    async def _apply_exit_fill(
        self,
        order: Order,
        position: Position,
        total_qty: Decimal,
        avg_price: Decimal,
        commission: Decimal,
        ts: datetime,
        fully_filled: bool,
    ) -> None:
        if PositionState(position.state) is PositionState.ABIERTA:
            try:
                await self._positions.mark_closing(position.id)
            except InvalidTransitionError as exc:
                self._log.error(
                    "fill_handler_exit_bad_state",
                    position_id=str(position.id),
                    state=position.state,
                    error=str(exc),
                )
                return
        if not fully_filled:
            self._log.info(
                "fill_handler_exit_partial",
                symbol=order.symbol,
                filled_qty=str(total_qty),
                target_qty=str(order.qty),
            )
            return

        position_side = PositionSide(position.side)
        gross = _gross_pnl(
            position_side, position.avg_entry_price, avg_price, position.qty
        )
        # `position.commissions` already holds the entry commission
        # (stamped by `mark_open`); add this exit order's commission
        # for the round-trip total.
        total_commission = position.commissions + commission
        try:
            await self._positions.mark_closed(
                position.id,
                avg_exit_price=avg_price,
                gross_pnl=gross,
                commissions=total_commission,
                closed_at=ts,
            )
        except InvalidTransitionError as exc:
            self._log.error(
                "fill_handler_close_bad_state",
                position_id=str(position.id),
                state=position.state,
                error=str(exc),
            )
            return
        await self._pnl.record_closed_position(
            day=ts.date(), gross_pnl=gross, commissions=total_commission
        )
        self._log.info(
            "fill_handler_position_closed",
            position_id=str(position.id),
            symbol=order.symbol,
            avg_exit_price=str(avg_price),
            gross_pnl=str(gross),
            commissions=str(total_commission),
            net_pnl=str(gross - total_commission),
        )

    async def _on_order_status_locked(self, status: BrokerOrderStatus) -> None:
        if status.status not in _CANCELLED_STATES:
            return
        order = await self._orders.get_by_ib_order_id(status.ib_order_id)
        if order is None:
            return
        await self._orders.set_status(
            order.id, status.status, ts_cancelled=self._clock()
        )

        position = await self._positions.get_open_position()
        if position is None:
            return
        if PositionState(position.state) is not PositionState.ABRIENDO:
            return
        if not self._is_entry(order, position):
            return

        total_qty, avg_price, commission = await self._fills.order_fill_summary(
            order.id
        )
        if total_qty == 0:
            await self._positions.mark_cancelled(
                position.id, closed_at=self._clock()
            )
            self._log.info(
                "fill_handler_entry_released",
                position_id=str(position.id),
                symbol=order.symbol,
                reason=status.status.value,
            )
            return
        # Entry partially filled, then cancelled: we hold a real (but
        # smaller) position. Promote it to ABIERTA so it is not stuck
        # in ABRIENDO. The bracket children were sized for the full
        # quantity — that mismatch is a reconciliation concern, hence
        # the CRITICAL log.
        try:
            await self._positions.mark_open(
                position.id,
                avg_entry_price=avg_price,
                qty=total_qty,
                commissions=commission,
            )
        except InvalidTransitionError:
            return
        self._log.critical(
            "fill_handler_partial_entry_cancelled",
            position_id=str(position.id),
            symbol=order.symbol,
            filled_qty=str(total_qty),
            target_qty=str(order.qty),
        )

    def _is_entry(self, order: Order, position: Position) -> bool:
        return OrderSide(order.side) is _entry_side(PositionSide(position.side))

    async def _resolve_order(self, ib_order_id: int) -> Order | None:
        """Look up an order, retrying briefly to absorb a persist race.

        The router/flattener submit to IBKR and then `await` the DB
        write; a fast fill can deliver its callback before that write
        commits. A short bounded retry closes the window without a
        durable buffer.
        """
        for attempt in range(self._lookup_attempts):
            order = await self._orders.get_by_ib_order_id(ib_order_id)
            if order is not None:
                return order
            if attempt + 1 < self._lookup_attempts:
                await asyncio.sleep(self._lookup_delay)
        return None


__all__ = ["BrokerFill", "BrokerOrderStatus", "FillHandler"]
