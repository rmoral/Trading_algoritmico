"""Unit + property tests for `tradingbot.data.indicators`.

The deterministic tests pin specific numerical results computed by
hand. The Hypothesis property tests assert invariants the math must
always satisfy regardless of the input shape — they are the main
guard against subtle off-by-one or precision regressions.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tradingbot.data import (
    CompletedBar,
    VolumeBucket,
    atr,
    ema,
    session_vwap,
    volume_profile,
)
from tradingbot.persistence.enums import BarResolution

# ---------- helpers ----------


def _bar(
    *,
    ts: datetime,
    o: str,
    h: str,
    low: str,
    c: str,
    vol: str = "1000",
    wap: str | None = None,
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
        wap=Decimal(wap) if wap is not None else None,
    )


def _sequence(ohlcv: list[tuple[str, str, str, str, str, str | None]]) -> list[CompletedBar]:
    base = datetime(2026, 5, 16, 14, 30, tzinfo=UTC)
    return [
        _bar(
            ts=base + timedelta(minutes=i),
            o=o,
            h=h,
            low=low,
            c=c,
            vol=vol,
            wap=wap,
        )
        for i, (o, h, low, c, vol, wap) in enumerate(ohlcv)
    ]


# ---------- session_vwap ----------


def test_vwap_empty_is_none() -> None:
    assert session_vwap([]) is None


def test_vwap_single_bar_uses_wap() -> None:
    bar = _bar(
        ts=datetime(2026, 5, 16, tzinfo=UTC),
        o="100",
        h="101",
        low="99",
        c="100.5",
        vol="500",
        wap="100.2",
    )
    assert session_vwap([bar]) == Decimal("100.2")


def test_vwap_weights_by_volume() -> None:
    """Two bars: wap 100 vol 100; wap 110 vol 300 -> (100*100 + 110*300)/400 = 107.5."""
    bars = _sequence(
        [
            ("100", "101", "99", "100", "100", "100"),
            ("110", "111", "109", "110", "300", "110"),
        ]
    )
    assert session_vwap(bars) == Decimal("107.5")


def test_vwap_falls_back_to_typical_when_wap_missing() -> None:
    # wap=None -> typical = (H + L + C) / 3 = (101 + 99 + 100) / 3 = 100
    bar = _bar(
        ts=datetime(2026, 5, 16, tzinfo=UTC),
        o="100",
        h="101",
        low="99",
        c="100",
        vol="500",
        wap=None,
    )
    assert session_vwap([bar]) == Decimal("100")


def test_vwap_zero_volume_returns_none() -> None:
    bars = _sequence([("100", "101", "99", "100", "0", "100")])
    assert session_vwap(bars) is None


# ---------- ema ----------


def test_ema_period_must_be_positive() -> None:
    with pytest.raises(ValueError):
        ema([], 0)


def test_ema_not_enough_bars_is_none() -> None:
    bars = _sequence([("100", "101", "99", "100", "100", "100")])
    assert ema(bars, 5) is None


def test_ema_seed_equals_sma_when_only_period_bars() -> None:
    """For exactly `period` bars the EMA equals the SMA of closes."""
    bars = _sequence(
        [
            ("100", "101", "99", "100", "100", None),
            ("101", "102", "100", "102", "100", None),
            ("102", "103", "101", "104", "100", None),
            ("103", "104", "102", "106", "100", None),
        ]
    )
    # SMA of [100, 102, 104, 106] = 103
    assert ema(bars, 4) == Decimal("103")


def test_ema_handles_constant_series() -> None:
    bars = _sequence([("100", "100", "100", "100", "100", None)] * 30)
    # All closes equal -> EMA equals the constant exactly.
    value = ema(bars, 20)
    assert value == Decimal("100")


# ---------- atr ----------


def test_atr_needs_period_plus_one_bars() -> None:
    bars = _sequence([("100", "101", "99", "100", "100", None)] * 14)
    assert atr(bars, period=14) is None
    bars2 = _sequence([("100", "101", "99", "100", "100", None)] * 15)
    assert atr(bars2, period=14) is not None


def test_atr_is_zero_when_no_movement() -> None:
    bars = _sequence([("100", "100", "100", "100", "100", None)] * 20)
    value = atr(bars, period=14)
    assert value == Decimal("0")


def test_atr_simple_case() -> None:
    # 3 bars, period=2.
    # Bar 1: H=101 L=99 C=100
    # Bar 2: H=102 L=100 C=101 -> TR2 = max(2, |102-100|, |100-100|) = 2
    # Bar 3: H=103 L=101 C=102 -> TR3 = max(2, |103-101|, |101-101|) = 2
    # Seed ATR (period=2) = (TR2 + TR3) / 2 = 2 -> no later bars so final ATR = 2.
    bars = _sequence(
        [
            ("100", "101", "99", "100", "100", None),
            ("100", "102", "100", "101", "100", None),
            ("101", "103", "101", "102", "100", None),
        ]
    )
    assert atr(bars, period=2) == Decimal("2")


def test_atr_period_must_be_positive() -> None:
    with pytest.raises(ValueError):
        atr([], 0)


# ---------- volume_profile ----------


def test_volume_profile_empty() -> None:
    assert volume_profile([], 5) == []


def test_volume_profile_zero_buckets() -> None:
    bars = _sequence([("100", "101", "99", "100", "100", None)])
    assert volume_profile(bars, 0) == []


def test_volume_profile_uniform_spread() -> None:
    """One bar from 100 to 110 with volume 100 split into 5 buckets of width 2.

    Each bucket gets 100 * (2 / 10) = 20.
    """
    bars = _sequence([("100", "110", "100", "105", "100", None)])
    profile = volume_profile(bars, 5)
    assert len(profile) == 5
    for bucket in profile:
        assert bucket.volume == Decimal("20")
    # Bucket centers at 101, 103, 105, 107, 109.
    assert [b.price for b in profile] == [
        Decimal("101"),
        Decimal("103"),
        Decimal("105"),
        Decimal("107"),
        Decimal("109"),
    ]


def test_volume_profile_single_price_bar() -> None:
    bars = _sequence([("100", "100", "100", "100", "300", None)])
    profile = volume_profile(bars, 5)
    assert len(profile) == 1  # collapsed to a single bucket
    assert profile[0].volume == Decimal("300")


def test_volume_profile_conserves_total_volume() -> None:
    bars = _sequence(
        [
            ("100", "104", "100", "102", "200", None),
            ("102", "105", "101", "103", "100", None),
            ("103", "107", "102", "106", "300", None),
        ]
    )
    profile = volume_profile(bars, 7)
    total = sum((b.volume for b in profile), Decimal(0))
    assert total == Decimal("600")  # 200 + 100 + 300


# ---------- property tests ----------

_PRICES = st.decimals(
    min_value=Decimal("1"),
    max_value=Decimal("10000"),
    allow_nan=False,
    allow_infinity=False,
    places=2,
)
_VOLUMES = st.decimals(
    min_value=Decimal("1"),
    max_value=Decimal("1000000"),
    allow_nan=False,
    allow_infinity=False,
    places=0,
)


@st.composite
def _bar_strategy(draw: st.DrawFn) -> CompletedBar:
    low = draw(_PRICES)
    high = low + draw(
        st.decimals(
            min_value=Decimal("0.01"),
            max_value=Decimal("100"),
            allow_nan=False,
            allow_infinity=False,
            places=2,
        )
    )
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
        volume=draw(_VOLUMES),
        wap=None,
    )


@given(st.lists(_bar_strategy(), min_size=1, max_size=50))
@settings(suppress_health_check=[HealthCheck.too_slow], deadline=None, max_examples=50)
def test_vwap_within_min_low_and_max_high(bars: list[CompletedBar]) -> None:
    value = session_vwap(bars)
    assert value is not None
    lo = min(b.low for b in bars)
    hi = max(b.high for b in bars)
    assert lo <= value <= hi


@given(st.lists(_bar_strategy(), min_size=15, max_size=50))
@settings(suppress_health_check=[HealthCheck.too_slow], deadline=None, max_examples=50)
def test_atr_is_non_negative(bars: list[CompletedBar]) -> None:
    value = atr(bars, period=14)
    assert value is not None
    assert value >= Decimal(0)


@given(st.lists(_bar_strategy(), min_size=20, max_size=50))
@settings(suppress_health_check=[HealthCheck.too_slow], deadline=None, max_examples=50)
def test_ema_within_min_and_max_close(bars: list[CompletedBar]) -> None:
    value = ema(bars, period=20)
    assert value is not None
    lo = min(b.close for b in bars)
    hi = max(b.close for b in bars)
    # EMA is a weighted average -> must lie within the range.
    assert lo <= value <= hi


@given(st.lists(_bar_strategy(), min_size=1, max_size=30), st.integers(min_value=1, max_value=12))
@settings(suppress_health_check=[HealthCheck.too_slow], deadline=None, max_examples=50)
def test_volume_profile_total_equals_input_volume(
    bars: list[CompletedBar], n_buckets: int
) -> None:
    profile = volume_profile(bars, n_buckets)
    assert isinstance(profile, list)
    assert all(isinstance(b, VolumeBucket) for b in profile)
    total_in = sum((b.volume for b in bars), Decimal(0))
    total_out = sum((b.volume for b in profile), Decimal(0))
    # The uniform-spread distribution must be volume-preserving.
    diff = abs(total_in - total_out)
    # Allow tiny rounding from Decimal divisions (28-digit default precision).
    assert diff < Decimal("0.0001"), f"diff={diff} total_in={total_in} total_out={total_out}"
