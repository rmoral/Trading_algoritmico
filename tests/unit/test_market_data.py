"""Unit tests for `MarketDataService`.

Both `BarSource` and `BarSink` are replaced with in-memory fakes so
the test suite never touches IB Gateway or Postgres.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tradingbot.data import CompletedBar, MarketDataService
from tradingbot.persistence.enums import BarResolution


class FakeBarSource:
    """Replays canned bars per (symbol, resolution).

    After the canned list is exhausted, blocks forever so the
    consumer task stays alive (mirrors a live IBKR subscription that
    waits for the next bar).
    """

    def __init__(
        self,
        bars: dict[tuple[str, BarResolution], list[CompletedBar]] | None = None,
        *,
        block_after: bool = True,
    ) -> None:
        self._bars = bars or {}
        self._block_after = block_after

    async def stream(
        self, symbol: str, resolution: BarResolution
    ) -> AsyncIterator[CompletedBar]:
        for bar in self._bars.get((symbol, resolution), []):
            yield bar
            await asyncio.sleep(0)  # allow consumer to interleave
        if self._block_after:
            await asyncio.Event().wait()  # block until cancelled


class FakeBarSink:
    """Collects inserted bars in memory."""

    def __init__(self) -> None:
        self.inserted: list[CompletedBar] = []

    async def insert_bar(self, bar: CompletedBar) -> None:
        self.inserted.append(bar)


def _bar(
    symbol: str,
    resolution: BarResolution,
    ts: datetime,
    *,
    close: str = "100.00",
    volume: str = "1000",
) -> CompletedBar:
    return CompletedBar(
        symbol=symbol,
        resolution=resolution,
        ts=ts,
        open=Decimal("99.50"),
        high=Decimal("100.50"),
        low=Decimal("99.00"),
        close=Decimal(close),
        volume=Decimal(volume),
        wap=Decimal("99.80"),
        count=42,
    )


async def _wait_for(
    predicate: object, timeout: float = 1.0, interval: float = 0.005
) -> None:
    """Spin until `predicate()` is truthy or timeout."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if callable(predicate) and predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError("predicate did not become truthy in time")


@pytest.mark.asyncio
async def test_subscribe_streams_and_persists_bars() -> None:
    now = datetime(2026, 5, 16, 14, 30, tzinfo=UTC)
    canned = [
        _bar("AAPL", BarResolution.M1, now, close="100.0"),
        _bar("AAPL", BarResolution.M1, now + timedelta(minutes=1), close="100.5"),
        _bar("AAPL", BarResolution.M1, now + timedelta(minutes=2), close="101.0"),
    ]
    source = FakeBarSource({("AAPL", BarResolution.M1): canned})
    sink = FakeBarSink()
    service = MarketDataService(
        source, sink, resolutions=(BarResolution.M1,), buffer_size=10
    )

    await service.subscribe("AAPL")
    await _wait_for(lambda: len(sink.inserted) >= 3)

    assert [str(b.close) for b in sink.inserted] == ["100.0", "100.5", "101.0"]
    buf = service.get_recent_bars("AAPL", BarResolution.M1)
    assert len(buf) == 3
    assert buf[-1].close == Decimal("101.0")

    await service.stop()


@pytest.mark.asyncio
async def test_subscribe_is_idempotent() -> None:
    canned = [_bar("AAPL", BarResolution.M1, datetime(2026, 5, 16, tzinfo=UTC))]
    source = FakeBarSource({("AAPL", BarResolution.M1): canned})
    sink = FakeBarSink()
    service = MarketDataService(source, sink, resolutions=(BarResolution.M1,))

    await service.subscribe("AAPL")
    await service.subscribe("AAPL")  # second call must not duplicate the task

    await _wait_for(lambda: len(sink.inserted) >= 1)
    assert len(sink.inserted) == 1  # only one stream

    await service.stop()


@pytest.mark.asyncio
async def test_unsubscribe_cancels_and_drops_buffer() -> None:
    canned = [_bar("AAPL", BarResolution.M1, datetime(2026, 5, 16, tzinfo=UTC))]
    source = FakeBarSource({("AAPL", BarResolution.M1): canned})
    sink = FakeBarSink()
    service = MarketDataService(source, sink, resolutions=(BarResolution.M1,))

    await service.subscribe("AAPL")
    await _wait_for(lambda: len(sink.inserted) >= 1)

    assert service.is_subscribed("AAPL") is True
    await service.unsubscribe("AAPL")
    assert service.is_subscribed("AAPL") is False
    assert service.get_recent_bars("AAPL", BarResolution.M1) == []


@pytest.mark.asyncio
async def test_multiple_resolutions_run_concurrently() -> None:
    now = datetime(2026, 5, 16, 14, 30, tzinfo=UTC)
    canned = {
        ("AAPL", BarResolution.M1): [
            _bar("AAPL", BarResolution.M1, now),
            _bar("AAPL", BarResolution.M1, now + timedelta(minutes=1)),
        ],
        ("AAPL", BarResolution.M5): [_bar("AAPL", BarResolution.M5, now)],
        ("AAPL", BarResolution.M15): [_bar("AAPL", BarResolution.M15, now)],
    }
    source = FakeBarSource(canned)
    sink = FakeBarSink()
    service = MarketDataService(source, sink)  # all three resolutions

    await service.subscribe("AAPL")
    await _wait_for(lambda: len(sink.inserted) >= 4)

    by_res: dict[BarResolution, int] = dict.fromkeys(
        (BarResolution.M1, BarResolution.M5, BarResolution.M15), 0
    )
    for bar in sink.inserted:
        by_res[bar.resolution] += 1
    assert by_res == {BarResolution.M1: 2, BarResolution.M5: 1, BarResolution.M15: 1}

    await service.stop()


@pytest.mark.asyncio
async def test_buffer_respects_size_cap() -> None:
    now = datetime(2026, 5, 16, 14, 30, tzinfo=UTC)
    canned = [
        _bar("AAPL", BarResolution.M1, now + timedelta(minutes=i)) for i in range(10)
    ]
    source = FakeBarSource({("AAPL", BarResolution.M1): canned})
    sink = FakeBarSink()
    service = MarketDataService(
        source, sink, resolutions=(BarResolution.M1,), buffer_size=3
    )

    await service.subscribe("AAPL")
    await _wait_for(lambda: len(sink.inserted) >= 10)

    buf = service.get_recent_bars("AAPL", BarResolution.M1)
    assert len(buf) == 3
    # The 7 oldest were evicted; we keep bars 7, 8, 9 (0-indexed).
    assert buf[0].ts == now + timedelta(minutes=7)
    assert buf[-1].ts == now + timedelta(minutes=9)

    await service.stop()


@pytest.mark.asyncio
async def test_sink_failure_does_not_break_stream() -> None:
    """One bad insert must not stop subsequent bars from flowing."""
    now = datetime(2026, 5, 16, 14, 30, tzinfo=UTC)
    canned = [
        _bar("AAPL", BarResolution.M1, now + timedelta(minutes=i)) for i in range(3)
    ]
    source = FakeBarSource({("AAPL", BarResolution.M1): canned})

    class FlakySink:
        def __init__(self) -> None:
            self.calls = 0
            self.inserted: list[CompletedBar] = []

        async def insert_bar(self, bar: CompletedBar) -> None:
            self.calls += 1
            if self.calls == 2:  # fail on the second bar
                raise RuntimeError("db down")
            self.inserted.append(bar)

    sink = FlakySink()
    service = MarketDataService(source, sink, resolutions=(BarResolution.M1,))

    await service.subscribe("AAPL")
    await _wait_for(lambda: sink.calls >= 3)

    # All three reached the sink; one was rejected. The buffer still
    # has all three because buffering happens before the sink.
    assert sink.calls == 3
    assert len(sink.inserted) == 2
    assert len(service.get_recent_bars("AAPL", BarResolution.M1)) == 3

    await service.stop()


@pytest.mark.asyncio
async def test_stop_unsubscribes_every_symbol() -> None:
    canned = {
        ("AAPL", BarResolution.M1): [
            _bar("AAPL", BarResolution.M1, datetime(2026, 5, 16, tzinfo=UTC)),
        ],
        ("MSFT", BarResolution.M1): [
            _bar("MSFT", BarResolution.M1, datetime(2026, 5, 16, tzinfo=UTC)),
        ],
    }
    source = FakeBarSource(canned)
    sink = FakeBarSink()
    service = MarketDataService(source, sink, resolutions=(BarResolution.M1,))

    await service.subscribe("AAPL")
    await service.subscribe("MSFT")
    await _wait_for(lambda: len(sink.inserted) >= 2)
    await service.stop()

    assert service.is_subscribed("AAPL") is False
    assert service.is_subscribed("MSFT") is False
