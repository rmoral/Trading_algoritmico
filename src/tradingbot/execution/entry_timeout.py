"""`EntryTimeoutWatcher`: cancel a marketable-limit entry that won't fill.

The discovery strategy enters with a marketable LIMIT. In normal book
conditions it fills in well under a second; if it is still unfilled
after `entry_limit_cancel_seconds` the market has moved away from the
level and the entry thesis is stale.

This task polls for an `ABRIENDO` position older than that timeout
and cancels its still-working orders. The cancellation event then
releases the position `ABRIENDO -> CERRADA` through the `FillHandler`,
freeing the single-position slot so discovery can resume — instead of
the bot sitting idle behind a stuck entry until the end-of-day
flatten.

A partially-filled entry that is cancelled is handled by the
`FillHandler` too: it promotes the position to `ABIERTA` with the
quantity that did fill.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from tradingbot.logging_setup import get_logger
from tradingbot.persistence.enums import PositionState
from tradingbot.persistence.models import Position
from tradingbot.persistence.repositories import OrderRepository, PositionsRepository

_Clock = Callable[[], datetime]

DEFAULT_POLL_SECONDS: float = 2.0


@runtime_checkable
class OrderCanceller(Protocol):
    """The single broker action this watcher needs."""

    async def cancel_order(self, ib_order_id: int) -> None: ...


class EntryTimeoutWatcher:
    """Poll for stale `ABRIENDO` entries and cancel them."""

    def __init__(
        self,
        *,
        positions_repo: PositionsRepository,
        order_repo: OrderRepository,
        canceller: OrderCanceller,
        timeout_seconds: int,
        clock: _Clock = lambda: datetime.now(UTC),
        poll_seconds: float = DEFAULT_POLL_SECONDS,
    ) -> None:
        self._positions = positions_repo
        self._orders = order_repo
        self._canceller = canceller
        self._timeout_seconds = timeout_seconds
        self._clock = clock
        self._poll_seconds = poll_seconds
        self._stop = asyncio.Event()
        self._log = get_logger(__name__)

    def stop(self) -> None:
        """Ask `run()` to exit at the next poll boundary."""
        self._stop.set()

    async def run(self) -> None:
        """Poll `tick()` every `poll_seconds` until `stop()` is called."""
        self._log.info(
            "entry_timeout_watcher_started",
            timeout_seconds=self._timeout_seconds,
            poll_seconds=self._poll_seconds,
        )
        while not self._stop.is_set():
            try:
                await self.tick()
            except Exception as exc:  # noqa: BLE001 - log and keep polling
                self._log.error(
                    "entry_timeout_watcher_tick_failed",
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
        self._log.info("entry_timeout_watcher_stopped")

    async def tick(self) -> None:
        """One poll: cancel the entry if it has been ABRIENDO too long."""
        position = await self._positions.get_open_position()
        if position is None:
            return
        if PositionState(position.state) is not PositionState.ABRIENDO:
            return
        age_seconds = (self._clock() - position.opened_at).total_seconds()
        if age_seconds < self._timeout_seconds:
            return
        await self._cancel_entry(position, age_seconds)

    async def _cancel_entry(self, position: Position, age_seconds: float) -> None:
        working = await self._orders.list_working_for_symbol(
            position.symbol, since=position.opened_at
        )
        cancelled: list[int] = []
        for order in working:
            if order.ib_order_id is not None:
                await self._canceller.cancel_order(order.ib_order_id)
                cancelled.append(order.ib_order_id)
        if cancelled:
            self._log.info(
                "entry_timeout_cancel",
                position_id=str(position.id),
                symbol=position.symbol,
                age_seconds=round(age_seconds, 1),
                cancelled_ib_order_ids=cancelled,
            )


__all__ = ["EntryTimeoutWatcher", "OrderCanceller"]
