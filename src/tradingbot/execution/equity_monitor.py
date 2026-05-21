"""`EquityMonitor`: feed account equity snapshots into the `EquityTracker`.

A background task that periodically pulls the IBKR account summary,
extracts `NetLiquidation` (total account value, including the open
position's unrealised P&L), and records it so the risk manager's
drawdown circuit breaker has live data.

Kept separate from `AccountStateLogger` to respect the layering:
this task lives in the execution layer and may depend on the
connector, whereas the connector must not reach up into execution.
The extra once-a-minute `accountSummary` call is negligible.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from tradingbot.connector.ib_client import IBClient
from tradingbot.execution.equity_tracker import EquityTracker
from tradingbot.logging_setup import get_logger

_Clock = Callable[[], datetime]

DEFAULT_INTERVAL_SECONDS: float = 60.0
_NET_LIQUIDATION_TAG: str = "NetLiquidation"


class EquityMonitor:
    """Poll IBKR account equity and record it for drawdown tracking."""

    def __init__(
        self,
        *,
        ib_client: IBClient,
        equity_tracker: EquityTracker,
        clock: _Clock = lambda: datetime.now(UTC),
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
    ) -> None:
        self._ib = ib_client
        self._tracker = equity_tracker
        self._clock = clock
        self._interval_seconds = interval_seconds
        self._stop = asyncio.Event()
        self._log = get_logger(__name__)

    def stop(self) -> None:
        """Ask `run()` to exit at the next interval boundary."""
        self._stop.set()

    async def run(self) -> None:
        """Snapshot equity once per interval until `stop()` is called."""
        self._log.info(
            "equity_monitor_started", interval_seconds=self._interval_seconds
        )
        while not self._stop.is_set():
            try:
                await self.tick()
            except Exception as exc:  # noqa: BLE001 - log and keep polling
                self._log.error(
                    "equity_monitor_tick_failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self._interval_seconds
                )
                break
            except TimeoutError:
                continue
        self._log.info("equity_monitor_stopped")

    async def tick(self) -> None:
        """One poll: read `NetLiquidation` and record it."""
        if not self._ib.is_connected():
            return
        summary = await self._ib.get_account_summary()
        raw = summary.get(_NET_LIQUIDATION_TAG)
        if raw is None:
            self._log.warning("equity_monitor_no_net_liquidation")
            return
        try:
            equity = Decimal(raw)
        except InvalidOperation:
            self._log.warning("equity_monitor_unparseable_equity", value=raw)
            return
        await self._tracker.record(equity, now=self._clock())


__all__ = ["EquityMonitor"]
