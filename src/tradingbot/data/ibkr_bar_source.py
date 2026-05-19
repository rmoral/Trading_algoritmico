"""`IBKRBarSource`: production `BarSource` over `ib_insync`.

Subscribes to one resolution per `(symbol, resolution)` pair via
`IB.reqHistoricalDataAsync(keepUpToDate=True)`. `ib_insync` then
keeps the returned `BarDataList` live, firing `updateEvent` whenever
a new bar is appended or the in-progress bar is updated.

We bridge `ib_insync`'s eventkit signal into an `asyncio.Queue` so
the rest of the system consumes a plain `AsyncIterator[CompletedBar]`
that satisfies `tradingbot.data.BarSource`.

Only completed bars are yielded — we never expose the partially
formed current bar. A bar is "completed" the moment a newer one
appears after it in the list.

This module is the only place we touch `ib_insync` for market data;
strategy / detector / engine never see the broker library directly.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from tradingbot.data.bars import CompletedBar
from tradingbot.logging_setup import get_logger
from tradingbot.persistence.enums import BarResolution

if TYPE_CHECKING:
    from tradingbot.connector.ib_client import IBClient

# IBKR's `barSizeSetting` strings for the three resolutions we use.
_BAR_SIZE_FOR_RESOLUTION: dict[BarResolution, str] = {
    BarResolution.M1: "1 min",
    BarResolution.M5: "5 mins",
    BarResolution.M15: "15 mins",
}

# How much history to pre-load at subscription time. CLAUDE.md §6
# wants 240 minutes of 1-minute bars so EMA200 warms up; for the
# higher timeframes we ask for the same duration in minutes (IBKR
# caps each request at 2_000 bars, well above what we need).
_HISTORY_DURATION: dict[BarResolution, str] = {
    BarResolution.M1: "4 H",
    BarResolution.M5: "4 H",
    BarResolution.M15: "4 H",
}


class IBKRBarSource:
    """Yield completed bars from IBKR for one or more `(symbol, resolution)`.

    Build one instance per `IBClient`; call `stream` once per
    subscription. The returned async iterator never terminates until
    cancelled.

    Caller is responsible for cancelling the consumer task when it
    no longer wants bars; `MarketDataService.unsubscribe()` does
    this.
    """

    def __init__(
        self,
        ib_client: IBClient,
        *,
        use_rth: bool = False,
        what_to_show: str = "TRADES",
    ) -> None:
        self._client = ib_client
        self._use_rth = use_rth
        self._what_to_show = what_to_show
        self._log = get_logger(__name__)

    async def stream(
        self, symbol: str, resolution: BarResolution
    ) -> AsyncIterator[CompletedBar]:
        """Subscribe and yield bars as they complete.

        Pre-loads `_HISTORY_DURATION` worth of historical bars and
        yields each one (in chronological order, excluding the
        partial last bar). Then keeps yielding as IBKR appends new
        completed bars.
        """
        from ib_insync import Stock  # imported lazily for testability

        contract = Stock(symbol, exchange="SMART", currency="USD")
        bar_size = _BAR_SIZE_FOR_RESOLUTION[resolution]
        duration = _HISTORY_DURATION[resolution]

        ib = self._client.ib  # underlying `ib_insync.IB`-like
        bars_list = await ib.reqHistoricalDataAsync(  # type: ignore[attr-defined]
            contract,
            endDateTime="",
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow=self._what_to_show,
            useRTH=self._use_rth,
            formatDate=1,
            keepUpToDate=True,
        )

        self._log.info(
            "ibkr_bars_subscribed",
            symbol=symbol,
            resolution=resolution.value,
            history_bars=len(bars_list),
        )

        queue: asyncio.Queue[CompletedBar] = asyncio.Queue()

        # Replay the historical bars (last one is the still-forming
        # partial bar; skip it).
        for raw in list(bars_list)[:-1]:
            queue.put_nowait(_to_completed_bar(raw, symbol, resolution))

        # `ib_insync` exposes an eventkit signal; subscribing pushes
        # new completed bars into the queue. We track `last_yielded`
        # so we never re-emit a bar (the partial bar updates the
        # last element in place; only when the LIST GROWS by one
        # does the previously-last bar become a completed bar).
        previous_len = len(bars_list)
        last_yielded_ts = (
            bars_list[-2].date if previous_len >= 2 else None
        )

        def on_update(updated_bars: Any, has_new_bar: bool) -> None:
            nonlocal previous_len, last_yielded_ts
            n = len(updated_bars)
            # The penultimate bar becomes "completed" any time the
            # list grew; emit it. Otherwise just refresh the
            # partial-bar bookkeeping.
            if n > previous_len and n >= 2:
                completed = updated_bars[-2]
                if last_yielded_ts is None or completed.date > last_yielded_ts:
                    queue.put_nowait(
                        _to_completed_bar(completed, symbol, resolution)
                    )
                    last_yielded_ts = completed.date
                previous_len = n

        bars_list.updateEvent += on_update
        try:
            while True:
                yield await queue.get()
        finally:
            bars_list.updateEvent -= on_update
            try:
                ib.cancelHistoricalData(bars_list)  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001 - cleanup, log only
                self._log.warning(
                    "ibkr_bars_cancel_failed",
                    symbol=symbol,
                    resolution=resolution.value,
                    error=str(exc),
                )
            self._log.info(
                "ibkr_bars_unsubscribed",
                symbol=symbol,
                resolution=resolution.value,
            )


def _to_completed_bar(
    raw: Any, symbol: str, resolution: BarResolution
) -> CompletedBar:
    """Convert an `ib_insync.BarData` into our domain `CompletedBar`.

    `raw.date` is a `datetime` already in UTC when `formatDate=1`
    is used with intraday bars (per the IBKR docs). `raw.average`
    is the WAP for that bar; `raw.barCount` is the trade count.
    Volume is in shares (not lots; IBKR uses 1-share units for US
    equities post-2021).
    """
    return CompletedBar(
        symbol=symbol,
        resolution=resolution,
        ts=raw.date,
        open=Decimal(str(raw.open)),
        high=Decimal(str(raw.high)),
        low=Decimal(str(raw.low)),
        close=Decimal(str(raw.close)),
        volume=Decimal(str(raw.volume)),
        wap=Decimal(str(raw.average)) if raw.average is not None else None,
        count=int(raw.barCount) if raw.barCount is not None else None,
    )
