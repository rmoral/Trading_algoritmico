"""Periodic IBKR account summary logger.

Runs alongside `IBClient.run()`. Every `interval_seconds` (default
60), pulls the cached account summary and emits a structured log line
plus updates Prometheus gauges. Satisfies the Phase 1 exit criterion
that the bot "logs account state every minute".

Tags exposed as gauges (USD): NetLiquidation, BuyingPower, TotalCashValue.
"""

from __future__ import annotations

import asyncio

from prometheus_client import Gauge

from tradingbot.connector.ib_client import IBClient
from tradingbot.logging_setup import get_logger

DEFAULT_INTERVAL_SECONDS: float = 60.0

ACCOUNT_NET_LIQUIDATION: Gauge = Gauge(
    "tradingbot_account_net_liquidation_usd",
    "IBKR account NetLiquidation in USD.",
)
ACCOUNT_BUYING_POWER: Gauge = Gauge(
    "tradingbot_account_buying_power_usd",
    "IBKR account BuyingPower in USD.",
)
ACCOUNT_TOTAL_CASH: Gauge = Gauge(
    "tradingbot_account_total_cash_usd",
    "IBKR account TotalCashValue in USD.",
)

_TAG_TO_GAUGE: dict[str, Gauge] = {
    "NetLiquidation": ACCOUNT_NET_LIQUIDATION,
    "BuyingPower": ACCOUNT_BUYING_POWER,
    "TotalCashValue": ACCOUNT_TOTAL_CASH,
}


class AccountStateLogger:
    """Background task that periodically snapshots IBKR account state."""

    def __init__(
        self,
        ib_client: IBClient,
        *,
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
    ) -> None:
        self._ib = ib_client
        self._interval_seconds = interval_seconds
        self._stop_event = asyncio.Event()
        self._log = get_logger(__name__)

    def stop(self) -> None:
        self._stop_event.set()

    async def run(self) -> None:
        """Snapshot once per interval until `stop()` is called."""
        while not self._stop_event.is_set():
            await self._snapshot()
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self._interval_seconds
                )
                return
            except TimeoutError:
                continue

    async def _snapshot(self) -> None:
        if not self._ib.is_connected():
            self._log.info("account_state_skip_disconnected")
            return
        summary = await self._ib.get_account_summary()
        if not summary:
            self._log.info("account_state_empty")
            return
        self._log.info("account_state_snapshot", **summary)
        for tag, gauge in _TAG_TO_GAUGE.items():
            raw = summary.get(tag)
            if raw is None:
                continue
            try:
                gauge.set(float(raw))
            except (ValueError, TypeError):
                self._log.warning(
                    "account_state_unparseable_value", tag=tag, value=raw
                )
