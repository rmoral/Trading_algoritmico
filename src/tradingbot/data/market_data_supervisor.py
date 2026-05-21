"""`MarketDataSupervisor`: keep the bar feed pointed at the active asset.

The operator picks the day's symbol in the web app, which writes to
`active_asset_selections`. This task polls that selection and, when
it changes, unsubscribes the previous symbol's bar streams and
subscribes the new one — so the strategy engine always finds bars
already buffered for whatever the operator chose.

Subscriptions are deferred until IBKR is connected: subscribing while
the gateway is down would start a stream that immediately fails. Once
`connected()` returns True the next poll picks the symbol up.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Protocol, runtime_checkable

from tradingbot.data.market_data import MarketDataService
from tradingbot.logging_setup import get_logger

DEFAULT_POLL_SECONDS: float = 5.0


@runtime_checkable
class ActiveSymbolSource(Protocol):
    """Minimal read API the supervisor needs.

    `tradingbot.persistence.repositories.ActiveAssetRepository`
    satisfies it structurally.
    """

    async def get_active_symbol(self) -> str | None: ...


class MarketDataSupervisor:
    """Reconcile `MarketDataService` subscriptions with the active asset."""

    def __init__(
        self,
        active_asset: ActiveSymbolSource,
        market_data: MarketDataService,
        *,
        connected: Callable[[], bool] | None = None,
        poll_seconds: float = DEFAULT_POLL_SECONDS,
    ) -> None:
        self._active_asset = active_asset
        self._market_data = market_data
        self._connected = connected
        self._poll_seconds = poll_seconds
        self._current: str | None = None
        self._stop = asyncio.Event()
        self._log = get_logger(__name__)

    def stop(self) -> None:
        """Ask `run()` to exit at the next poll boundary."""
        self._stop.set()

    async def run(self) -> None:
        """Poll the active asset until `stop()` is called."""
        self._log.info("market_data_supervisor_started", poll=self._poll_seconds)
        while not self._stop.is_set():
            try:
                await self.sync()
            except Exception as exc:  # noqa: BLE001 - log and continue
                self._log.error(
                    "market_data_supervisor_sync_failed",
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
        if self._current is not None:
            await self._market_data.unsubscribe(self._current)
            self._current = None
        self._log.info("market_data_supervisor_stopped")

    async def sync(self) -> None:
        """Subscribe / unsubscribe so the feed matches the active asset."""
        if self._connected is not None and not self._connected():
            # Gateway is down; defer until it comes back.
            return

        symbol = await self._active_asset.get_active_symbol()
        if symbol == self._current:
            return

        if self._current is not None:
            await self._market_data.unsubscribe(self._current)
        if symbol is not None:
            await self._market_data.subscribe(symbol)
        self._log.info(
            "market_data_active_symbol_changed",
            previous=self._current,
            current=symbol,
        )
        self._current = symbol


__all__ = ["ActiveSymbolSource", "MarketDataSupervisor"]
