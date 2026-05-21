"""`HaltMonitor`: detect trading halts on the active asset via IBKR.

A LULD (Limit-Up Limit-Down) halt is reported by IBKR on the live
market-data stream — the `halted` tick. The bot's bar feed
(`reqHistoricalData` + `keepUpToDate`) does not carry it, so this
monitor opens a dedicated `reqMktData` subscription for the active
symbol and polls its `halted` value into the `HaltStateStore`.

It follows the operator's asset choice the same way the market-data
supervisor does: when the active symbol changes it cancels the old
subscription and opens one for the new symbol.

A `halted` value that IBKR has not yet reported (NaN / unknown) is
read as "not halted": halts are rare and the tick arrives quickly,
whereas treating "unknown" as halted would block all trading until
the first tick lands.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from tradingbot.connector.ib_client import IBClient
from tradingbot.execution.halt_state import HaltStateStore
from tradingbot.logging_setup import get_logger
from tradingbot.persistence.repositories import ActiveAssetRepository

_Clock = Callable[[], datetime]

DEFAULT_POLL_SECONDS: float = 3.0


def _is_halted(raw_halted: object) -> bool:
    """Interpret `ib_insync` `Ticker.halted`.

    IBKR reports 0 = not halted, 1 = general halt, 2 = volatility
    halt, and -1 / NaN = unknown. Anything strictly positive is a
    halt; unknown is treated as not halted.
    """
    if raw_halted is None:
        return False
    try:
        value = float(raw_halted)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return math.isfinite(value) and value > 0


class HaltMonitor:
    """Poll the active asset's `halted` tick into the `HaltStateStore`."""

    def __init__(
        self,
        *,
        ib_client: IBClient,
        active_asset_repo: ActiveAssetRepository,
        halt_store: HaltStateStore,
        clock: _Clock = lambda: datetime.now(UTC),
        poll_seconds: float = DEFAULT_POLL_SECONDS,
    ) -> None:
        self._ib = ib_client
        self._active_asset = active_asset_repo
        self._halt_store = halt_store
        self._clock = clock
        self._poll_seconds = poll_seconds
        self._stop = asyncio.Event()
        self._subscribed_symbol: str | None = None
        self._contract: Any = None
        self._ticker: Any = None
        self._log = get_logger(__name__)

    def stop(self) -> None:
        """Ask `run()` to exit at the next poll boundary."""
        self._stop.set()

    async def run(self) -> None:
        """Poll `tick()` every `poll_seconds` until `stop()` is called."""
        self._log.info("halt_monitor_started", poll_seconds=self._poll_seconds)
        while not self._stop.is_set():
            try:
                await self.tick()
            except Exception as exc:  # noqa: BLE001 - log and keep polling
                self._log.error(
                    "halt_monitor_tick_failed",
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
        self._unsubscribe()
        self._log.info("halt_monitor_stopped")

    async def tick(self) -> None:
        """One poll: keep the subscription on the active asset, record halt."""
        if not self._ib.is_connected():
            return
        symbol = await self._active_asset.get_active_symbol()
        if symbol is None:
            self._unsubscribe()
            return
        if symbol != self._subscribed_symbol:
            self._subscribe(symbol)
        if self._ticker is None:
            return
        halted = _is_halted(getattr(self._ticker, "halted", None))
        await self._halt_store.update(halted, now=self._clock())

    def _subscribe(self, symbol: str) -> None:
        from ib_insync import Stock

        self._unsubscribe()
        contract = Stock(symbol, "SMART", "USD")
        self._ticker = self._ib.ib.reqMktData(contract)
        self._contract = contract
        self._subscribed_symbol = symbol
        self._log.info("halt_monitor_subscribed", symbol=symbol)

    def _unsubscribe(self) -> None:
        if self._subscribed_symbol is not None and self._contract is not None:
            try:
                self._ib.ib.cancelMktData(self._contract)
            except Exception as exc:  # noqa: BLE001 - best effort on teardown
                self._log.warning(
                    "halt_monitor_cancel_failed", error=str(exc)
                )
        self._ticker = None
        self._contract = None
        self._subscribed_symbol = None


__all__ = ["HaltMonitor"]
