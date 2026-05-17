"""Unit tests for `SRDetector`.

The market-data and sink dependencies are replaced with in-memory
fakes. No DB, no IBKR. The detector itself is a thin orchestrator
on top of pure functions, so the tests focus on:

- It pulls the right slices from MarketDataService for each
  resolution given a lookback.
- It produces a DetectedLevel for every confirmed cross-timeframe
  pivot, with strength in [0, 100] and components broken out.
- It calls the sink for each detected level.
- A failing sink does not crash the pipeline; remaining levels
  still propagate to the caller's return value.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tradingbot.data import CompletedBar, DetectedLevel, SRDetector, SRDetectorConfig
from tradingbot.persistence.enums import BarResolution, SRKind

# ---------- fakes ----------


class FakeMarketData:
    """Minimal stand-in for MarketDataService.

    Only exposes `get_recent_bars` — the only method the detector
    consumes.
    """

    def __init__(
        self,
        bars_by_resolution: dict[BarResolution, list[CompletedBar]],
    ) -> None:
        self._bars = bars_by_resolution
        self.calls: list[tuple[str, BarResolution, int | None]] = []

    def get_recent_bars(
        self, symbol: str, resolution: BarResolution, n: int | None = None
    ) -> list[CompletedBar]:
        self.calls.append((symbol, resolution, n))
        all_bars = self._bars.get(resolution, [])
        if n is None:
            return all_bars
        return all_bars[-n:]


class FakeSink:
    def __init__(self) -> None:
        self.received: list[DetectedLevel] = []

    async def upsert_level(self, level: DetectedLevel) -> None:
        self.received.append(level)


class FlakySink:
    """Sink whose nth call raises."""

    def __init__(self, fail_on_call: int) -> None:
        self.fail_on_call = fail_on_call
        self.calls = 0
        self.received: list[DetectedLevel] = []

    async def upsert_level(self, level: DetectedLevel) -> None:
        self.calls += 1
        if self.calls == self.fail_on_call:
            raise RuntimeError("db down")
        self.received.append(level)


# ---------- helpers ----------


def _bar(
    *,
    ts: datetime,
    resolution: BarResolution,
    o: str,
    h: str,
    low: str,
    c: str,
    vol: str = "1000",
) -> CompletedBar:
    return CompletedBar(
        symbol="AAPL",
        resolution=resolution,
        ts=ts,
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(low),
        close=Decimal(c),
        volume=Decimal(vol),
        wap=Decimal(c),
    )


def _mountain_series(
    resolution: BarResolution,
    *,
    n_each_side: int = 1,
    peak_high: str = "105",
    side_high: str = "102",
    low: str = "94",
) -> list[CompletedBar]:
    """Build a ▲-shape sequence so the middle bar is a strict high pivot.

    Lows are tied so the middle bar is NOT a low pivot.
    """
    base = datetime(2026, 5, 16, 14, 0, tzinfo=UTC)
    minutes_per_bar = {BarResolution.M1: 1, BarResolution.M5: 5, BarResolution.M15: 15}
    step = timedelta(minutes=minutes_per_bar[resolution])
    bars: list[CompletedBar] = []
    for i in range(n_each_side):
        bars.append(
            _bar(
                ts=base + step * i,
                resolution=resolution,
                o="100",
                h=side_high,
                low=low,
                c="100",
            )
        )
    mid_idx = n_each_side
    bars.append(
        _bar(
            ts=base + step * mid_idx,
            resolution=resolution,
            o="103",
            h=peak_high,
            low=low,
            c="104",
        )
    )
    for i in range(1, n_each_side + 1):
        bars.append(
            _bar(
                ts=base + step * (mid_idx + i),
                resolution=resolution,
                o="100",
                h=side_high,
                low=low,
                c="100",
            )
        )
    return bars


# ---------- tests ----------


def _config(**overrides: object) -> SRDetectorConfig:
    base: dict[str, object] = {
        "sr_lookback_minutes": 60,
        "sr_pivot_window": 1,
        "sr_level_tolerance_pct": Decimal("0.05"),
        "indicator_history_minutes": 240,
    }
    base.update(overrides)
    return SRDetectorConfig(**base)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_detect_returns_confirmed_levels_with_strength_in_range() -> None:
    """5m and 15m series both peak at 105 (within 0.05% of each other) -> confirmed."""
    bars_15m = _mountain_series(BarResolution.M15, peak_high="105", side_high="102")
    bars_5m = _mountain_series(BarResolution.M5, peak_high="105.02", side_high="102")
    bars_1m = [
        _bar(
            ts=datetime(2026, 5, 16, 14, 0, tzinfo=UTC) + timedelta(minutes=i),
            resolution=BarResolution.M1,
            o="104.95",
            h="105.02",
            low="104.98",
            c="105.0",
        )
        for i in range(30)
    ]

    md = FakeMarketData(
        {
            BarResolution.M15: bars_15m,
            BarResolution.M5: bars_5m,
            BarResolution.M1: bars_1m,
        }
    )
    sink = FakeSink()
    detector = SRDetector(md, sink, _config())  # type: ignore[arg-type]
    levels = await detector.detect("AAPL")

    assert len(levels) == 1
    level = levels[0]
    assert level.symbol == "AAPL"
    assert level.kind == SRKind.RESISTANCE
    assert level.price == Decimal("105")
    assert Decimal(0) <= level.strength <= Decimal(100)
    # Components are broken out individually.
    assert Decimal(0) <= level.components.clean_touches <= Decimal(100)
    assert Decimal(0) <= level.components.rejection_quality <= Decimal(100)

    # Sink received exactly the same payload.
    assert sink.received == [level]


@pytest.mark.asyncio
async def test_detect_with_no_confirmed_levels_returns_empty() -> None:
    """5m series peaks far from 15m peak -> nothing confirmed."""
    bars_15m = _mountain_series(BarResolution.M15, peak_high="105")
    bars_5m = _mountain_series(BarResolution.M5, peak_high="200", side_high="150", low="100")

    md = FakeMarketData(
        {
            BarResolution.M15: bars_15m,
            BarResolution.M5: bars_5m,
            BarResolution.M1: [],
        }
    )
    sink = FakeSink()
    detector = SRDetector(md, sink, _config())  # type: ignore[arg-type]
    levels = await detector.detect("AAPL")

    assert levels == []
    assert sink.received == []


@pytest.mark.asyncio
async def test_detect_pulls_correct_bar_counts() -> None:
    """sr_lookback_minutes=60 -> request 4 × 15m, 12 × 5m, 60 × 1m.

    indicator_history_minutes=240 -> request 240 × 1m.
    """
    md = FakeMarketData(
        {
            BarResolution.M15: [],
            BarResolution.M5: [],
            BarResolution.M1: [],
        }
    )
    sink = FakeSink()
    detector = SRDetector(md, sink, _config())  # type: ignore[arg-type]
    await detector.detect("AAPL")

    by_resolution = {res: n for _, res, n in md.calls}
    assert by_resolution[BarResolution.M15] == 4   # 60 / 15
    assert by_resolution[BarResolution.M5] == 12   # 60 / 5
    assert by_resolution[BarResolution.M1] == 240  # indicator_history_minutes


def _double_peak(resolution: BarResolution, *, peak1: str, peak2: str) -> list[CompletedBar]:
    """5-bar series with exactly two strict pivot highs at peak1 and peak2.

    Layout: side, peak1, dip, peak2, side. Lows are kept flat at 90
    EXCEPT for the dip in the middle (low=92), so the dip is NOT a
    strict local minimum — its neighbours have lower lows. This
    yields exactly two pivots (both RESISTANCE), no SUPPORT pivots.
    """
    base = datetime(2026, 5, 16, 14, 0, tzinfo=UTC)
    step = timedelta(
        minutes={BarResolution.M1: 1, BarResolution.M5: 5, BarResolution.M15: 15}[resolution]
    )
    return [
        _bar(ts=base + step * 0, resolution=resolution, o="95", h="100", low="90", c="95"),
        _bar(ts=base + step * 1, resolution=resolution, o="103", h=peak1, low="90", c="103"),
        _bar(ts=base + step * 2, resolution=resolution, o="95", h="100", low="92", c="95"),
        _bar(ts=base + step * 3, resolution=resolution, o="108", h=peak2, low="90", c="108"),
        _bar(ts=base + step * 4, resolution=resolution, o="95", h="100", low="90", c="95"),
    ]


@pytest.mark.asyncio
async def test_detect_propagates_through_sink_failure() -> None:
    """If the sink raises on level i, the remaining levels still return."""
    bars_15m = _double_peak(BarResolution.M15, peak1="105", peak2="110")
    # 5m peaks slightly off in price but within the 0.05% tolerance band.
    bars_5m = _double_peak(BarResolution.M5, peak1="105.02", peak2="110.02")

    md = FakeMarketData(
        {
            BarResolution.M15: bars_15m,
            BarResolution.M5: bars_5m,
            BarResolution.M1: [],
        }
    )
    sink = FlakySink(fail_on_call=1)
    # Lookback 90 minutes so 15m has 6 bars and both peaks fit.
    detector = SRDetector(md, sink, _config(sr_lookback_minutes=90))  # type: ignore[arg-type]
    levels = await detector.detect("AAPL")

    # Two levels detected even though the first sink call raised.
    assert len(levels) == 2
    # First persist call failed; second succeeded.
    assert sink.calls == 2
    assert len(sink.received) == 1


@pytest.mark.asyncio
async def test_detect_uses_injected_clock() -> None:
    """detected_at comes from the injected clock, not real wall time."""
    bars_15m = _mountain_series(BarResolution.M15)
    bars_5m = _mountain_series(BarResolution.M5, peak_high="105.02", side_high="102")
    fixed = datetime(2030, 1, 1, 0, 0, tzinfo=UTC)
    md = FakeMarketData(
        {
            BarResolution.M15: bars_15m,
            BarResolution.M5: bars_5m,
            BarResolution.M1: [],
        }
    )
    sink = FakeSink()
    detector = SRDetector(md, sink, _config(), clock=lambda: fixed)  # type: ignore[arg-type]
    levels = await detector.detect("AAPL")
    assert all(level.detected_at == fixed for level in levels)
