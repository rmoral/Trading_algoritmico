"""Tests for `IBKRBarSource` with a fake `ib_insync.IB`.

These pin the bridge between `ib_insync`'s `BarDataList.updateEvent`
and our `AsyncIterator[CompletedBar]`. Real-broker behaviour will be
validated empirically once Phase 0 is complete; for now we mock the
shapes documented by `ib_insync`.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from tradingbot.connector.ib_client import IBClient
from tradingbot.data.ibkr_bar_source import IBKRBarSource, _to_completed_bar
from tradingbot.persistence.enums import BarResolution


@dataclass
class FakeBarData:
    """Subset of `ib_insync.BarData` we depend on."""

    date: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    average: float | None
    barCount: int | None


class FakeUpdateEvent:
    """Mimics eventkit's `+= callback` / `-= callback`."""

    def __init__(self) -> None:
        self._handlers: list[Any] = []

    def __iadd__(self, handler: Any) -> FakeUpdateEvent:
        self._handlers.append(handler)
        return self

    def __isub__(self, handler: Any) -> FakeUpdateEvent:
        if handler in self._handlers:
            self._handlers.remove(handler)
        return self

    def fire(self, bars_list: Any, has_new_bar: bool) -> None:
        for h in list(self._handlers):
            h(bars_list, has_new_bar)


class FakeBarsList(list[FakeBarData]):
    """List-like with a `updateEvent` attribute, like `ib_insync.BarDataList`."""

    def __init__(self, initial: list[FakeBarData]) -> None:
        super().__init__(initial)
        self.updateEvent = FakeUpdateEvent()


class FakeIB:
    """Stub `ib_insync.IB` for the bar source."""

    def __init__(self, bars_list: FakeBarsList) -> None:
        self._bars_list = bars_list
        self.req_calls: list[dict[str, Any]] = []
        self.cancel_calls: list[FakeBarsList] = []

    async def reqHistoricalDataAsync(
        self,
        contract: Any,
        *,
        endDateTime: str,
        durationStr: str,
        barSizeSetting: str,
        whatToShow: str,
        useRTH: bool,
        formatDate: int,
        keepUpToDate: bool,
    ) -> FakeBarsList:
        self.req_calls.append(
            {
                "contract": contract,
                "barSizeSetting": barSizeSetting,
                "durationStr": durationStr,
                "whatToShow": whatToShow,
                "useRTH": useRTH,
                "keepUpToDate": keepUpToDate,
            }
        )
        return self._bars_list

    def cancelHistoricalData(self, bars_list: FakeBarsList) -> None:
        self.cancel_calls.append(bars_list)


def _stub_stock() -> None:
    """Install a tiny `ib_insync.Stock` so the bar source's lazy import works."""

    class _Stock:
        def __init__(self, symbol: str, exchange: str, currency: str) -> None:
            self.symbol = symbol
            self.exchange = exchange
            self.currency = currency

    import sys
    import types

    mod = types.ModuleType("ib_insync")
    mod.Stock = _Stock  # type: ignore[attr-defined]
    sys.modules["ib_insync"] = mod


_stub_stock()


def _bar(
    *,
    minute: int,
    close: float = 100.0,
    average: float | None = 100.0,
) -> FakeBarData:
    return FakeBarData(
        date=datetime(2026, 5, 16, 14, minute, tzinfo=UTC),
        open=close - 0.1,
        high=close + 0.2,
        low=close - 0.2,
        close=close,
        volume=1000.0,
        average=average,
        barCount=10,
    )


def _fake_client(ib: FakeIB) -> IBClient:
    """Wrap the fake IB in a MagicMock IBClient so the source can call `.ib`."""
    client = MagicMock(spec=IBClient)
    client.ib = ib
    return cast(IBClient, client)


# ---------- _to_completed_bar ----------


def test_to_completed_bar_copies_fields() -> None:
    raw = _bar(minute=30, close=100.5, average=100.4)
    bar = _to_completed_bar(raw, "AAPL", BarResolution.M1)
    assert bar.symbol == "AAPL"
    assert bar.resolution == BarResolution.M1
    assert bar.ts == raw.date
    assert bar.open == Decimal("100.4")
    assert bar.high == Decimal("100.7")
    assert bar.low == Decimal("100.3")
    assert bar.close == Decimal("100.5")
    assert bar.wap == Decimal("100.4")
    assert bar.count == 10


def test_to_completed_bar_handles_missing_wap_and_count() -> None:
    raw = _bar(minute=30, average=None)
    raw.barCount = None
    bar = _to_completed_bar(raw, "AAPL", BarResolution.M1)
    assert bar.wap is None
    assert bar.count is None


# ---------- stream ----------


@pytest.mark.asyncio
async def test_stream_yields_history_then_new_bars() -> None:
    """Pre-load yields N-1 (last is partial); new bars stream as the list grows."""
    bars_list = FakeBarsList(
        [
            _bar(minute=30, close=100.0),
            _bar(minute=31, close=100.5),
            _bar(minute=32, close=100.8),  # partial — never yielded as-is
        ]
    )
    ib = FakeIB(bars_list)
    source = IBKRBarSource(_fake_client(ib))

    received: list[Any] = []

    async def consume() -> None:
        async for bar in source.stream("AAPL", BarResolution.M1):
            received.append(bar)

    task = asyncio.create_task(consume())

    # Wait for the historical bars to drain.
    for _ in range(50):
        if len(received) >= 2:
            break
        await asyncio.sleep(0.005)

    # First two completed (history minus partial).
    assert len(received) == 2
    assert received[0].close == Decimal("100.0")
    assert received[1].close == Decimal("100.5")

    # Simulate the broker appending a new bar: minute 32 becomes
    # completed, minute 33 is the new partial.
    bars_list.append(_bar(minute=33, close=101.0))
    bars_list.updateEvent.fire(bars_list, True)
    for _ in range(50):
        if len(received) >= 3:
            break
        await asyncio.sleep(0.005)
    assert received[2].close == Decimal("100.8")

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    # Cleanup: cancelHistoricalData was called once during teardown.
    assert ib.cancel_calls == [bars_list]


@pytest.mark.asyncio
async def test_stream_does_not_double_emit() -> None:
    """An update without list growth (partial bar tick) emits nothing."""
    bars_list = FakeBarsList(
        [
            _bar(minute=30, close=100.0),
            _bar(minute=31, close=100.5),
        ]
    )
    ib = FakeIB(bars_list)
    source = IBKRBarSource(_fake_client(ib))
    received: list[Any] = []

    async def consume() -> None:
        async for bar in source.stream("AAPL", BarResolution.M1):
            received.append(bar)

    task = asyncio.create_task(consume())
    for _ in range(50):
        if len(received) >= 1:
            break
        await asyncio.sleep(0.005)
    assert len(received) == 1

    # Fire a partial-bar tick: list does NOT grow.
    bars_list[-1].close = 100.6
    bars_list.updateEvent.fire(bars_list, False)
    await asyncio.sleep(0.02)
    assert len(received) == 1  # nothing new

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_request_uses_documented_parameters() -> None:
    bars_list = FakeBarsList([_bar(minute=30), _bar(minute=31)])
    ib = FakeIB(bars_list)
    source = IBKRBarSource(_fake_client(ib), use_rth=False, what_to_show="TRADES")

    async def consume() -> None:
        async for _ in source.stream("AAPL", BarResolution.M5):
            return

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.01)

    assert len(ib.req_calls) == 1
    call = ib.req_calls[0]
    assert call["barSizeSetting"] == "5 mins"
    assert call["durationStr"] == "4 H"
    assert call["whatToShow"] == "TRADES"
    assert call["useRTH"] is False
    assert call["keepUpToDate"] is True

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_cleanup_unsubscribes_listener() -> None:
    bars_list = FakeBarsList([_bar(minute=30), _bar(minute=31)])
    ib = FakeIB(bars_list)
    source = IBKRBarSource(_fake_client(ib))

    async def consume() -> None:
        async for _ in source.stream("AAPL", BarResolution.M1):
            return

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.02)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    # Listener was detached.
    assert bars_list.updateEvent._handlers == []
    # cancelHistoricalData called.
    assert ib.cancel_calls == [bars_list]


# Suppress unused-import warnings.
_ = timedelta
