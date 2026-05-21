"""`EndOfDayFlattener`: force-close any open position before the close.

The bot is a scalper — it never holds overnight. A few minutes
before the regular-hours close (`force_flatten_before_close_minutes`)
this task:

- for an `ABIERTA` position: cancels the live bracket children so
  they cannot double-exit, then submits a `MarketOrder` to flatten —
  one of the two `MarketOrder` paths sanctioned by CLAUDE.md §6;
- for an `ABRIENDO` position: cancels the unfilled entry so the bot
  is not filled into a position seconds before the bell. The
  cancellation event releases the position `ABRIENDO -> CERRADA`
  through the `FillHandler`.

A `CERRANDO` position is already exiting and is left alone, which
makes the tick idempotent: once a flatten is in flight, later ticks
do nothing.

The flatten `MarketOrder` is submitted directly, NOT through the
risk manager. The risk manager gates new exposure (CLAUDE.md §2
principle 2); closing exposure is never gated — a flatten that the
risk manager could refuse (e.g. once the daily-loss cap has tripped)
would strand the bot in a position it must not hold.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from tradingbot.execution.market_clock import MarketClock
from tradingbot.execution.order_router import SubmittedLeg
from tradingbot.logging_setup import get_logger
from tradingbot.persistence.enums import OrderSide, PositionSide, PositionState
from tradingbot.persistence.models import Position
from tradingbot.persistence.repositories import OrderRepository, PositionsRepository
from tradingbot.strategy.state_machine import InvalidTransitionError

_Clock = Callable[[], datetime]

DEFAULT_POLL_SECONDS: float = 30.0


@runtime_checkable
class FlattenExecutor(Protocol):
    """Broker actions the flattener needs, beyond bracket submission."""

    async def cancel_order(self, ib_order_id: int) -> None: ...

    async def submit_market_order(
        self, symbol: str, side: OrderSide, qty: Decimal
    ) -> SubmittedLeg: ...


def _exit_side(position_side: PositionSide) -> OrderSide:
    """The order side that CLOSES a position of the given direction."""
    return OrderSide.SELL if position_side is PositionSide.LONG else OrderSide.BUY


class EndOfDayFlattener:
    """Poll the market clock and flatten before the regular-hours close."""

    def __init__(
        self,
        *,
        positions_repo: PositionsRepository,
        order_repo: OrderRepository,
        executor: FlattenExecutor,
        market_clock: MarketClock,
        force_flatten_minutes: int,
        clock: _Clock = lambda: datetime.now(UTC),
        poll_seconds: float = DEFAULT_POLL_SECONDS,
    ) -> None:
        self._positions = positions_repo
        self._orders = order_repo
        self._executor = executor
        self._clock_calendar = market_clock
        self._force_flatten_minutes = force_flatten_minutes
        self._clock = clock
        self._poll_seconds = poll_seconds
        self._stop = asyncio.Event()
        self._log = get_logger(__name__)

    def stop(self) -> None:
        """Ask `run()` to exit at the next poll boundary."""
        self._stop.set()

    async def run(self) -> None:
        """Poll `tick()` every `poll_seconds` until `stop()` is called."""
        self._log.info("eod_flattener_started", poll_seconds=self._poll_seconds)
        while not self._stop.is_set():
            try:
                await self.tick()
            except Exception as exc:  # noqa: BLE001 - log and keep polling
                self._log.error(
                    "eod_flattener_tick_failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self._poll_seconds
                )
                break
            except TimeoutError:
                continue
        self._log.info("eod_flattener_stopped")

    async def tick(self) -> None:
        """One poll: flatten if inside the force-flatten window."""
        now = self._clock()
        minutes_to_close = self._clock_calendar.minutes_to_close(now)
        if minutes_to_close is None:
            return
        if minutes_to_close > self._force_flatten_minutes:
            return

        position = await self._positions.get_open_position()
        if position is None:
            return

        state = PositionState(position.state)
        if state is PositionState.ABIERTA:
            await self._flatten_open_position(position, now)
        elif state is PositionState.ABRIENDO:
            await self._cancel_unfilled_entry(position)
        # CERRANDO: an exit is already in flight — nothing to do.

    async def _flatten_open_position(
        self, position: Position, now: datetime
    ) -> None:
        # Cancel the live bracket children first so a stop/take-profit
        # cannot fill against the same shares the flatten will close.
        await self._cancel_working_orders(position)
        try:
            await self._positions.mark_closing(position.id)
        except InvalidTransitionError:
            # The position closed itself (a child filled) between the
            # get_open_position read and here — nothing left to flatten.
            self._log.info(
                "eod_flatten_skipped_already_closing",
                position_id=str(position.id),
            )
            return

        exit_side = _exit_side(PositionSide(position.side))
        leg = await self._executor.submit_market_order(
            position.symbol, exit_side, position.qty
        )
        await self._orders.insert_exit_market_order(
            order_id=leg.internal_id,
            ib_order_id=leg.ib_order_id,
            symbol=position.symbol,
            side=exit_side,
            qty=position.qty,
            ts=now,
        )
        self._log.info(
            "eod_flatten_submitted",
            position_id=str(position.id),
            symbol=position.symbol,
            side=exit_side.value,
            qty=str(position.qty),
            ib_order_id=leg.ib_order_id,
        )

    async def _cancel_unfilled_entry(self, position: Position) -> None:
        await self._cancel_working_orders(position)
        self._log.info(
            "eod_entry_cancelled",
            position_id=str(position.id),
            symbol=position.symbol,
        )

    async def _cancel_working_orders(self, position: Position) -> None:
        working = await self._orders.list_working_for_symbol(
            position.symbol, since=position.opened_at
        )
        for order in working:
            if order.ib_order_id is not None:
                await self._executor.cancel_order(order.ib_order_id)


__all__ = ["EndOfDayFlattener", "FlattenExecutor"]
