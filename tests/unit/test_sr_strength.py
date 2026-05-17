"""Unit + property tests for the S/R strength score components.

Deterministic tests pin numerical results computed by hand. Hypothesis
property tests assert always-true invariants: every component score
is in [0, 100], and the composite strength is also in [0, 100] when
the weights sum to 1.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tradingbot.data import (
    DEFAULT_WEIGHTS,
    CompletedBar,
    IndicatorSnapshot,
    StrengthWeights,
    clean_touches_score,
    compute_strength,
    ma_confluence_score,
    persistence_score,
    rejection_quality_score,
    volume_at_price_score,
)
from tradingbot.persistence.enums import BarResolution


def _bar(
    *,
    ts: datetime,
    o: str,
    h: str,
    low: str,
    c: str,
    vol: str = "1000",
) -> CompletedBar:
    return CompletedBar(
        symbol="X",
        resolution=BarResolution.M1,
        ts=ts,
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(low),
        close=Decimal(c),
        volume=Decimal(vol),
        wap=Decimal(c),
    )


def _series(rows: list[tuple[str, str, str, str, str]]) -> list[CompletedBar]:
    base = datetime(2026, 5, 16, 14, 30, tzinfo=UTC)
    return [
        _bar(ts=base + timedelta(minutes=i), o=o, h=h, low=low, c=c, vol=vol)
        for i, (o, h, low, c, vol) in enumerate(rows)
    ]


TOL = Decimal("0.05")  # 0.05% (= 0.05 in the percent units the API uses)


# ---------- clean_touches_score ----------


def test_clean_touches_empty_is_zero() -> None:
    assert clean_touches_score([], Decimal("100"), TOL) == Decimal(0)


def test_clean_touches_zero_level_is_zero() -> None:
    bars = _series([("100", "101", "99", "100", "1000")])
    assert clean_touches_score(bars, Decimal("0"), TOL) == Decimal(0)


def test_clean_touches_one_touch_scores_twenty() -> None:
    """1 of 5 saturation -> 20."""
    bars = _series(
        [
            ("99", "99.5", "98.5", "99", "100"),  # below band
            ("100", "100.04", "99.96", "100", "100"),  # touches
            ("101", "101.5", "100.5", "101", "100"),  # above band
            ("102", "102.5", "101.5", "102", "100"),  # above band
            ("103", "103.5", "102.5", "103", "100"),  # above band
        ]
    )
    assert clean_touches_score(bars, Decimal("100"), TOL) == Decimal(20)


def test_clean_touches_saturates_at_five() -> None:
    bars = _series([("100", "100.04", "99.96", "100", "100")] * 10)
    assert clean_touches_score(bars, Decimal("100"), TOL) == Decimal(100)


# ---------- volume_at_price_score ----------


def test_volume_at_price_empty_is_zero() -> None:
    assert volume_at_price_score([], Decimal("100"), TOL) == Decimal(0)


def test_volume_at_price_all_volume_at_level_saturates() -> None:
    """All volume sits in the band -> raw fraction = 1.0 -> saturated -> 100."""
    bars = _series([("100", "100.04", "99.96", "100", "1000")] * 3)
    assert volume_at_price_score(bars, Decimal("100"), TOL) == Decimal(100)


def test_volume_at_price_no_overlap_is_zero() -> None:
    """All volume far from level -> 0."""
    bars = _series([("90", "91", "89", "90", "1000")] * 3)
    assert volume_at_price_score(bars, Decimal("100"), TOL) == Decimal(0)


def test_volume_at_price_zero_total_volume_is_zero() -> None:
    bars = _series([("100", "100.04", "99.96", "100", "0")])
    assert volume_at_price_score(bars, Decimal("100"), TOL) == Decimal(0)


# ---------- ma_confluence_score ----------


def test_ma_confluence_no_indicators() -> None:
    assert ma_confluence_score(Decimal("100"), IndicatorSnapshot()) == Decimal(0)


def test_ma_confluence_one_indicator_close() -> None:
    """VWAP within 0.1% of level -> +25."""
    snapshot = IndicatorSnapshot(vwap=Decimal("100.05"))
    assert ma_confluence_score(Decimal("100"), snapshot) == Decimal(25)


def test_ma_confluence_all_four_saturate() -> None:
    snapshot = IndicatorSnapshot(
        vwap=Decimal("100"),
        ema20=Decimal("100"),
        ema50=Decimal("100"),
        ema200=Decimal("100"),
    )
    assert ma_confluence_score(Decimal("100"), snapshot) == Decimal(100)


def test_ma_confluence_far_indicators_ignored() -> None:
    snapshot = IndicatorSnapshot(vwap=Decimal("105"), ema20=Decimal("95"))
    assert ma_confluence_score(Decimal("100"), snapshot) == Decimal(0)


def test_ma_confluence_zero_indicator_ignored() -> None:
    snapshot = IndicatorSnapshot(vwap=Decimal("0"))
    assert ma_confluence_score(Decimal("100"), snapshot) == Decimal(0)


# ---------- persistence_score ----------


def test_persistence_no_break_saturates_at_sixty_minutes() -> None:
    """12 × 5min bars all in band -> 60 min held -> 100."""
    bars = _series([("100", "100.04", "99.96", "100", "100")] * 12)
    score = persistence_score(bars, Decimal("100"), TOL, minutes_per_bar=5)
    assert score == Decimal(100)


def test_persistence_recent_break_resets() -> None:
    """The most recent bar broke the band -> 0 bars held."""
    bars = _series(
        [
            ("100", "100.04", "99.96", "100", "100"),
            ("100", "100.04", "99.96", "100", "100"),
            # Last bar is fully above the band -> break
            ("110", "111", "110.5", "110.8", "100"),
        ]
    )
    score = persistence_score(bars, Decimal("100"), TOL, minutes_per_bar=1)
    assert score == Decimal(0)


def test_persistence_partial_hold() -> None:
    """3 of 5 most recent bars in band -> 3 min -> 5/60 saturation -> 5."""
    bars = _series(
        [
            ("110", "111", "110.5", "110.8", "100"),  # broken
            ("110", "111", "110.5", "110.8", "100"),  # broken
            ("100", "100.04", "99.96", "100", "100"),  # held
            ("100", "100.04", "99.96", "100", "100"),  # held
            ("100", "100.04", "99.96", "100", "100"),  # held (most recent)
        ]
    )
    # 3 × 1min / 60 = 5
    score = persistence_score(bars, Decimal("100"), TOL, minutes_per_bar=1)
    assert score == Decimal(5)


def test_persistence_minutes_per_bar_zero_is_zero() -> None:
    bars = _series([("100", "100.04", "99.96", "100", "100")])
    assert persistence_score(bars, Decimal("100"), TOL, minutes_per_bar=0) == Decimal(0)


# ---------- rejection_quality_score ----------


def test_rejection_no_touching_bars_is_zero() -> None:
    bars = _series([("90", "91", "89", "90", "100")])
    assert rejection_quality_score(bars, Decimal("100"), TOL) == Decimal(0)


def test_rejection_long_upper_wick_strong() -> None:
    """One bar with high in band, body small, wick large -> high ratio."""
    # high=100.02 (in band), open=99.98, close=99.99 -> body=0.01, upper wick = 0.03
    # ratio = 0.03 / 0.01 = 3.0 -> capped at 100 since saturation = 2.0
    bars = _series([("99.98", "100.02", "99.97", "99.99", "100")])
    score = rejection_quality_score(bars, Decimal("100"), TOL)
    assert score == Decimal(100)


def test_rejection_no_wick_is_zero() -> None:
    """A marubozu where body fills the entire H-L range -> no wicks -> 0."""
    # open=low=99.98 and close=high=100.02: both extremes are in the band,
    # but neither has a wick.
    bars = _series([("99.98", "100.02", "99.98", "100.02", "100")])
    score = rejection_quality_score(bars, Decimal("100"), TOL)
    assert score == Decimal(0)


# ---------- compute_strength ----------


def test_compute_strength_components_break_out() -> None:
    """Synthetic scenario where each component is computable from inputs."""
    bars = _series([("100", "100.04", "99.96", "100", "1000")] * 12)
    snapshot = IndicatorSnapshot(
        vwap=Decimal("100"),
        ema20=Decimal("100"),
        ema50=Decimal("100"),
        ema200=Decimal("100"),
    )
    strength, components = compute_strength(
        Decimal("100"),
        bars,
        snapshot,
        tolerance_pct=TOL,
        minutes_per_bar=5,
        weights=DEFAULT_WEIGHTS,
    )
    # 12 touches > 5 saturation -> 100
    assert components.clean_touches == Decimal(100)
    # All 4 MAs confluent -> 100
    assert components.ma_confluence == Decimal(100)
    # 12 * 5min = 60 min -> 100
    assert components.persistence == Decimal(100)
    # Composite must lie in [0, 100]
    assert Decimal(0) <= strength <= Decimal(100)


def test_compute_strength_with_custom_weights() -> None:
    """Custom weights still produce a value in [0, 100]."""
    bars = _series([("100", "100.04", "99.96", "100", "1000")] * 3)
    weights = StrengthWeights(
        clean_touches=Decimal("0.5"),
        volume_at_price=Decimal("0.5"),
        ma_confluence=Decimal("0"),
        persistence=Decimal("0"),
        rejection_quality=Decimal("0"),
    )
    strength, _ = compute_strength(
        Decimal("100"),
        bars,
        IndicatorSnapshot(),
        tolerance_pct=TOL,
        minutes_per_bar=1,
        weights=weights,
    )
    assert Decimal(0) <= strength <= Decimal(100)


# ---------- property tests ----------


@st.composite
def _bar_strategy(draw: st.DrawFn) -> CompletedBar:
    low = draw(
        st.decimals(
            min_value=Decimal("10"),
            max_value=Decimal("500"),
            allow_nan=False,
            allow_infinity=False,
            places=2,
        )
    )
    width = draw(
        st.decimals(
            min_value=Decimal("0.01"),
            max_value=Decimal("5"),
            allow_nan=False,
            allow_infinity=False,
            places=2,
        )
    )
    high = low + width
    open_ = draw(
        st.decimals(
            min_value=low, max_value=high, allow_nan=False, allow_infinity=False, places=2
        )
    )
    close = draw(
        st.decimals(
            min_value=low, max_value=high, allow_nan=False, allow_infinity=False, places=2
        )
    )
    return CompletedBar(
        symbol="X",
        resolution=BarResolution.M1,
        ts=datetime(2026, 5, 16, tzinfo=UTC),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=draw(
            st.decimals(
                min_value=Decimal("1"),
                max_value=Decimal("1000000"),
                allow_nan=False,
                allow_infinity=False,
                places=0,
            )
        ),
        wap=close,
    )


_LEVELS = st.decimals(
    min_value=Decimal("10"),
    max_value=Decimal("500"),
    allow_nan=False,
    allow_infinity=False,
    places=2,
)


@given(st.lists(_bar_strategy(), min_size=1, max_size=30), _LEVELS)
@settings(suppress_health_check=[HealthCheck.too_slow], deadline=None, max_examples=50)
def test_all_components_within_0_100(bars: list[CompletedBar], level: Decimal) -> None:
    snapshot = IndicatorSnapshot(
        vwap=level,
        ema20=level,
        ema50=level + Decimal("1"),
        ema200=None,
    )
    c1 = clean_touches_score(bars, level, TOL)
    c2 = volume_at_price_score(bars, level, TOL)
    c3 = ma_confluence_score(level, snapshot)
    c4 = persistence_score(bars, level, TOL, minutes_per_bar=1)
    c5 = rejection_quality_score(bars, level, TOL)
    for value in (c1, c2, c3, c4, c5):
        assert Decimal(0) <= value <= Decimal(100), f"out of range: {value}"


@given(st.lists(_bar_strategy(), min_size=1, max_size=30), _LEVELS)
@settings(suppress_health_check=[HealthCheck.too_slow], deadline=None, max_examples=50)
def test_composite_strength_within_0_100(
    bars: list[CompletedBar], level: Decimal
) -> None:
    snapshot = IndicatorSnapshot(vwap=level, ema20=level)
    strength, _ = compute_strength(
        level,
        bars,
        snapshot,
        tolerance_pct=TOL,
        minutes_per_bar=1,
        weights=DEFAULT_WEIGHTS,
    )
    assert Decimal(0) <= strength <= Decimal(100)
