"""Domain types for completed bars + the protocols `MarketDataService`
depends on.

A `CompletedBar` is what the rest of the system consumes. Whether it
originated from `ib_insync.reqHistoricalDataAsync` (production) or
from a canned fixture (tests) is irrelevant beyond this boundary.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from tradingbot.persistence.enums import BarResolution


@dataclass(frozen=True)
class CompletedBar:
    """A finished OHLCV bar at a given resolution.

    `ts` is the bar's start time in UTC. The bar covers
    `[ts, ts + resolution)`.
    """

    symbol: str
    resolution: BarResolution
    ts: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    wap: Decimal | None = None
    count: int | None = None


@runtime_checkable
class BarSource(Protocol):
    """Source of completed bars for a `(symbol, resolution)` pair.

    Implementations:
    - `IBKRBarSource` wraps `IB.reqHistoricalDataAsync(keepUpToDate=True)`.
    - Test doubles yield canned sequences.

    The stream yields each bar exactly once, in chronological order,
    and never yields the partially-formed current bar. A new bar is
    yielded only after the previous one is closed.
    """

    def stream(
        self, symbol: str, resolution: BarResolution
    ) -> AsyncIterator[CompletedBar]: ...


@runtime_checkable
class BarSink(Protocol):
    """Destination for completed bars.

    Production: `tradingbot.persistence.repositories.BarRepository`.
    Tests: an in-memory collector.
    """

    async def insert_bar(self, bar: CompletedBar) -> None: ...
