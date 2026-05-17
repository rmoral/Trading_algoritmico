"""Unit + property tests for pivot detection + cross-timeframe confirmation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tradingbot.data import CompletedBar, ConfirmedLevel, Pivot, cross_confirm, find_pivots
from tradingbot.persistence.enums import BarResolution, SRKind


def _bar(*, ts: datetime, h: str, low: str) -> CompletedBar:
    """OHLC where O/C are mid-range and volume is constant.

    Tests focus on pivot detection which only looks at H/L, but
    `CompletedBar` requires all fields.
    """
    high = Decimal(h)
    lo = Decimal(low)
    mid = (high + lo) / Decimal(2)
    return CompletedBar(
        symbol="X",
        resolution=BarResolution.M15,
        ts=ts,
        open=mid,
        high=high,
        low=lo,
        close=mid,
        volume=Decimal("1000"),
        wap=mid,
    )


def _series(highs_lows: list[tuple[str, str]], *, step_min: int = 15) -> list[CompletedBar]:
    base = datetime(2026, 5, 16, 14, 0, tzinfo=UTC)
    return [
        _bar(ts=base + timedelta(minutes=step_min * i), h=h, low=low)
        for i, (h, low) in enumerate(highs_lows)
    ]


# ---------- find_pivots ----------


def test_window_must_be_positive() -> None:
    with pytest.raises(ValueError):
        find_pivots([], window=0, timeframe=BarResolution.M15)


def test_returns_empty_when_too_short() -> None:
    bars = _series([("101", "99"), ("102", "98"), ("103", "97")])
    # window=2 needs >= 5 bars; we have 3.
    assert find_pivots(bars, window=2, timeframe=BarResolution.M15) == []


def test_detects_high_pivot() -> None:
    """Index 2 is a strict local max on `high`; lows tied to avoid a support pivot."""
    bars = _series([("100", "95"), ("102", "94"), ("105", "94"), ("102", "94"), ("100", "95")])
    pivots = find_pivots(bars, window=2, timeframe=BarResolution.M15)
    assert len(pivots) == 1
    assert pivots[0].kind == SRKind.RESISTANCE
    assert pivots[0].price == Decimal("105")
    assert pivots[0].ts == bars[2].ts
    assert pivots[0].timeframe == BarResolution.M15


def test_detects_low_pivot() -> None:
    """Index 2 is a strict local min on `low`; highs tied to avoid a resistance pivot."""
    bars = _series([("100", "98"), ("100", "96"), ("100", "94"), ("100", "96"), ("100", "98")])
    pivots = find_pivots(bars, window=2, timeframe=BarResolution.M15)
    assert len(pivots) == 1
    assert pivots[0].kind == SRKind.SUPPORT
    assert pivots[0].price == Decimal("94")


def test_strict_inequality_rejects_plateaus() -> None:
    """Ties in `high` should NOT produce a pivot."""
    bars = _series([("100", "98"), ("105", "97"), ("105", "96"), ("105", "97"), ("100", "98")])
    pivots = find_pivots(bars, window=2, timeframe=BarResolution.M15)
    # No bar's high is a STRICT max -> no pivot high.
    assert all(p.kind != SRKind.RESISTANCE for p in pivots)


def test_can_be_both_high_and_low_pivot() -> None:
    """A bar can simultaneously be the local max on H and local min on L."""
    bars = _series([("100", "98"), ("99", "97"), ("105", "94"), ("99", "97"), ("100", "98")])
    pivots = find_pivots(bars, window=2, timeframe=BarResolution.M15)
    assert {p.kind for p in pivots} == {SRKind.SUPPORT, SRKind.RESISTANCE}
    assert all(p.ts == bars[2].ts for p in pivots)


def test_window_of_one_works_with_three_bars() -> None:
    bars = _series([("100", "98"), ("105", "94"), ("100", "98")])
    pivots = find_pivots(bars, window=1, timeframe=BarResolution.M5)
    assert {p.kind for p in pivots} == {SRKind.SUPPORT, SRKind.RESISTANCE}
    assert all(p.timeframe == BarResolution.M5 for p in pivots)


def test_multiple_pivots_in_chronological_order() -> None:
    """Two clear high pivots separated by valleys."""
    bars = _series(
        [
            ("100", "95"),  # 0
            ("110", "95"),  # 1 high pivot (window=1)
            ("100", "95"),  # 2
            ("105", "95"),  # 3 high pivot (window=1)
            ("100", "95"),  # 4
        ]
    )
    pivots = [
        p
        for p in find_pivots(bars, window=1, timeframe=BarResolution.M15)
        if p.kind == SRKind.RESISTANCE
    ]
    assert [p.price for p in pivots] == [Decimal("110"), Decimal("105")]
    assert pivots[0].ts < pivots[1].ts


# ---------- cross_confirm ----------


def _pivot(
    *,
    timeframe: BarResolution,
    price: str,
    kind: SRKind,
    offset_min: int = 0,
) -> Pivot:
    return Pivot(
        timeframe=timeframe,
        ts=datetime(2026, 5, 16, 14, 0, tzinfo=UTC) + timedelta(minutes=offset_min),
        price=Decimal(price),
        kind=kind,
    )


def test_cross_confirm_pairs_within_tolerance() -> None:
    p15 = _pivot(timeframe=BarResolution.M15, price="100.00", kind=SRKind.RESISTANCE)
    p5 = _pivot(timeframe=BarResolution.M5, price="100.04", kind=SRKind.RESISTANCE)
    # 0.04 / 100 * 100 = 0.04% <= 0.05%
    confirmed = cross_confirm([p15], [p5], tolerance_pct=Decimal("0.05"))
    assert len(confirmed) == 1
    assert confirmed[0].price == Decimal("100.00")  # 15m price wins
    assert confirmed[0].kind == SRKind.RESISTANCE


def test_cross_confirm_rejects_outside_tolerance() -> None:
    p15 = _pivot(timeframe=BarResolution.M15, price="100", kind=SRKind.RESISTANCE)
    p5 = _pivot(timeframe=BarResolution.M5, price="101", kind=SRKind.RESISTANCE)
    # 1% > 0.05%
    assert cross_confirm([p15], [p5], tolerance_pct=Decimal("0.05")) == []


def test_cross_confirm_rejects_different_kinds() -> None:
    p15 = _pivot(timeframe=BarResolution.M15, price="100", kind=SRKind.RESISTANCE)
    p5 = _pivot(timeframe=BarResolution.M5, price="100", kind=SRKind.SUPPORT)
    assert cross_confirm([p15], [p5], tolerance_pct=Decimal("0.05")) == []


def test_cross_confirm_picks_closest_5m() -> None:
    p15 = _pivot(timeframe=BarResolution.M15, price="100", kind=SRKind.RESISTANCE)
    far = _pivot(timeframe=BarResolution.M5, price="100.04", kind=SRKind.RESISTANCE, offset_min=5)
    near = _pivot(timeframe=BarResolution.M5, price="100.01", kind=SRKind.RESISTANCE, offset_min=10)
    confirmed = cross_confirm([p15], [far, near], tolerance_pct=Decimal("0.05"))
    assert len(confirmed) == 1
    assert confirmed[0].pivot_5m is near


def test_cross_confirm_multiple_15m_pivots() -> None:
    p15a = _pivot(timeframe=BarResolution.M15, price="100", kind=SRKind.RESISTANCE)
    p15b = _pivot(
        timeframe=BarResolution.M15, price="110", kind=SRKind.RESISTANCE, offset_min=30
    )
    p5a = _pivot(timeframe=BarResolution.M5, price="100.02", kind=SRKind.RESISTANCE)
    p5b = _pivot(
        timeframe=BarResolution.M5, price="110.02", kind=SRKind.RESISTANCE, offset_min=35
    )
    confirmed = cross_confirm([p15a, p15b], [p5a, p5b], tolerance_pct=Decimal("0.05"))
    assert [c.price for c in confirmed] == [Decimal("100"), Decimal("110")]


def test_cross_confirm_empty_inputs() -> None:
    assert cross_confirm([], [], tolerance_pct=Decimal("0.05")) == []


# ---------- property tests ----------


@st.composite
def _bar_strategy(draw: st.DrawFn) -> CompletedBar:
    low = draw(
        st.decimals(
            min_value=Decimal("1"),
            max_value=Decimal("1000"),
            allow_nan=False,
            allow_infinity=False,
            places=2,
        )
    )
    width = draw(
        st.decimals(
            min_value=Decimal("0.01"),
            max_value=Decimal("20"),
            allow_nan=False,
            allow_infinity=False,
            places=2,
        )
    )
    high = low + width
    mid = (low + high) / Decimal(2)
    return CompletedBar(
        symbol="X",
        resolution=BarResolution.M15,
        ts=datetime(2026, 5, 16, tzinfo=UTC),
        open=mid,
        high=high,
        low=low,
        close=mid,
        volume=Decimal("1000"),
        wap=mid,
    )


@given(st.lists(_bar_strategy(), min_size=5, max_size=30), st.integers(min_value=1, max_value=3))
@settings(suppress_health_check=[HealthCheck.too_slow], deadline=None, max_examples=50)
def test_pivot_count_bounded_by_inner_bars(bars: list[CompletedBar], window: int) -> None:
    """A bar can be at most one pivot per kind. At most 2*(n-2*window) pivots total."""
    if len(bars) < 2 * window + 1:
        assert find_pivots(bars, window=window, timeframe=BarResolution.M15) == []
        return
    pivots = find_pivots(bars, window=window, timeframe=BarResolution.M15)
    assert len(pivots) <= 2 * (len(bars) - 2 * window)


@given(st.lists(_bar_strategy(), min_size=5, max_size=30), st.integers(min_value=1, max_value=3))
@settings(suppress_health_check=[HealthCheck.too_slow], deadline=None, max_examples=50)
def test_pivot_price_within_input_range(bars: list[CompletedBar], window: int) -> None:
    """Pivot prices come from bar.high or bar.low, never invented."""
    pivots = find_pivots(bars, window=window, timeframe=BarResolution.M15)
    all_highs = {b.high for b in bars}
    all_lows = {b.low for b in bars}
    for p in pivots:
        if p.kind == SRKind.RESISTANCE:
            assert p.price in all_highs
        else:
            assert p.price in all_lows


@given(
    st.lists(_bar_strategy(), min_size=3, max_size=15),
    st.lists(_bar_strategy(), min_size=3, max_size=15),
    st.decimals(
        min_value=Decimal("0.01"),
        max_value=Decimal("1"),
        allow_nan=False,
        allow_infinity=False,
        places=3,
    ),
)
@settings(suppress_health_check=[HealthCheck.too_slow], deadline=None, max_examples=50)
def test_cross_confirm_invariants(
    bars_15: list[CompletedBar],
    bars_5: list[CompletedBar],
    tolerance_pct: Decimal,
) -> None:
    p15 = find_pivots(bars_15, window=1, timeframe=BarResolution.M15)
    p5 = find_pivots(bars_5, window=1, timeframe=BarResolution.M5)
    confirmed = cross_confirm(p15, p5, tolerance_pct=tolerance_pct)

    assert isinstance(confirmed, list)
    assert all(isinstance(c, ConfirmedLevel) for c in confirmed)
    # At most one confirmation per 15m pivot.
    assert len(confirmed) <= len(p15)
    # Every confirmation respects tolerance and kind.
    for c in confirmed:
        diff_pct = abs(c.pivot_15m.price - c.pivot_5m.price) / c.pivot_15m.price * Decimal(100)
        assert diff_pct <= tolerance_pct
        assert c.pivot_15m.kind == c.pivot_5m.kind == c.kind
        assert c.price == c.pivot_15m.price
