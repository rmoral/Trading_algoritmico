"""Live indicators over the in-memory bar buffer.

Pure functions. Each takes a chronologically ordered list of
`CompletedBar` and returns a single value (or a structured result for
the volume profile). Stateless: re-call on each new bar.

Why pure functions instead of stateful objects? Two reasons:
1. Determinism makes them trivial to unit-test and to property-test
   with Hypothesis.
2. The bot only computes indicators when the strategy needs them
   (each tick of the discovery loop) — the cost is dominated by the
   bar buffer size, not by the cadence of calls.

IBKR's API exposes:
- WAP per bar (used directly here for session VWAP).
- Nothing else in the indicator family. Session-cumulative VWAP,
  EMA, ATR, Bollinger Bands, etc. are NOT available through
  reqHistoricalData — we compute them from the OHLC + WAP + volume
  IBKR does give us. This is documented behaviour of the TWS API,
  see CLAUDE.md §6.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from tradingbot.data.bars import CompletedBar


def session_vwap(bars: Sequence[CompletedBar]) -> Decimal | None:
    """Volume-weighted average price across `bars`.

    Uses IBKR's per-bar WAP when present:
        VWAP = sum(wap_i * volume_i) / sum(volume_i)
    Falls back to typical price ((H + L + C) / 3) when wap is None,
    which can happen for very old bars or non-TRADES `whatToShow`.

    Returns `None` if `bars` is empty or total volume is zero.
    """
    if not bars:
        return None
    total_pv = Decimal(0)
    total_v = Decimal(0)
    for bar in bars:
        price = bar.wap if bar.wap is not None else _typical_price(bar)
        total_pv += price * bar.volume
        total_v += bar.volume
    if total_v == 0:
        return None
    return total_pv / total_v


def ema(bars: Sequence[CompletedBar], period: int) -> Decimal | None:
    """Exponential moving average of closes over `period` bars.

    Seed = simple average of the first `period` closes; subsequent
    bars apply the standard recursion
        EMA_t = alpha * close_t + (1 - alpha) * EMA_{t-1}
    with alpha = 2 / (period + 1).

    Returns `None` if fewer than `period` bars are available.
    """
    if period < 1:
        raise ValueError(f"period must be >= 1; got {period}")
    if len(bars) < period:
        return None
    alpha = Decimal(2) / Decimal(period + 1)
    closes = [bar.close for bar in bars]
    value = sum(closes[:period], Decimal(0)) / Decimal(period)
    for close in closes[period:]:
        value = alpha * close + (Decimal(1) - alpha) * value
    return value


def atr(bars: Sequence[CompletedBar], period: int = 14) -> Decimal | None:
    """Wilder's Average True Range over `period` bars.

    True Range for bar i:
        TR_i = max(H_i - L_i, |H_i - C_{i-1}|, |L_i - C_{i-1}|)
    ATR uses Wilder smoothing:
        ATR_t = ((period - 1) * ATR_{t-1} + TR_t) / period
    seeded with the simple average of the first `period` TRs.

    Returns `None` if fewer than `period + 1` bars are available
    (one extra is needed to compute the first TR).
    """
    if period < 1:
        raise ValueError(f"period must be >= 1; got {period}")
    if len(bars) < period + 1:
        return None
    trs = [
        max(
            bars[i].high - bars[i].low,
            abs(bars[i].high - bars[i - 1].close),
            abs(bars[i].low - bars[i - 1].close),
        )
        for i in range(1, len(bars))
    ]
    value = sum(trs[:period], Decimal(0)) / Decimal(period)
    period_dec = Decimal(period)
    period_minus_one = Decimal(period - 1)
    for tr in trs[period:]:
        value = (period_minus_one * value + tr) / period_dec
    return value


@dataclass(frozen=True)
class VolumeBucket:
    """One bucket of the volume profile.

    `price` is the bucket's center; `volume` is the sum of bar
    volumes attributed to this bucket (uniform spread across the
    bar's H–L range).
    """

    price: Decimal
    volume: Decimal


def volume_profile(
    bars: Sequence[CompletedBar], n_buckets: int
) -> list[VolumeBucket]:
    """Volume-by-price histogram over `bars`, in ascending price order.

    Each bar's volume is spread uniformly across its H–L range. A
    bucket receives the fraction of bar volume corresponding to the
    overlap between [bucket_low, bucket_high) and [bar.low, bar.high].

    For a bar with H == L (a single price), all volume goes to the
    bucket containing that price.

    Returns an empty list if `bars` is empty or `n_buckets < 1`.
    """
    if not bars or n_buckets < 1:
        return []
    overall_low = min(bar.low for bar in bars)
    overall_high = max(bar.high for bar in bars)
    if overall_high <= overall_low:
        total_volume = sum((bar.volume for bar in bars), Decimal(0))
        return [VolumeBucket(price=overall_low, volume=total_volume)]

    n = Decimal(n_buckets)
    span = overall_high - overall_low
    bucket_size = span / n
    sums = [Decimal(0)] * n_buckets

    for bar in bars:
        if bar.high == bar.low:
            offset = (bar.low - overall_low) / bucket_size
            idx = min(int(offset), n_buckets - 1)
            sums[idx] += bar.volume
            continue
        bar_range = bar.high - bar.low
        for i in range(n_buckets):
            b_low = overall_low + bucket_size * Decimal(i)
            b_high = b_low + bucket_size
            overlap_low = max(b_low, bar.low)
            overlap_high = min(b_high, bar.high)
            if overlap_high > overlap_low:
                sums[i] += bar.volume * (overlap_high - overlap_low) / bar_range

    half = Decimal("0.5")
    return [
        VolumeBucket(
            price=overall_low + bucket_size * (Decimal(i) + half),
            volume=sums[i],
        )
        for i in range(n_buckets)
    ]


def _typical_price(bar: CompletedBar) -> Decimal:
    return (bar.high + bar.low + bar.close) / Decimal(3)
