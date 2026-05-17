"""Strength score for a candidate support/resistance level.

Score is a weighted sum of five 0-100 components (`CLAUDE.md §6`):

| Component         | Weight | Definition                                      |
|-------------------|-------:|-------------------------------------------------|
| Clean touches     |  35%   | Distinct bars intersecting [P ± tol]            |
| Volume at price   |  30%   | Fraction of session volume traded at the level  |
| MA confluence     |  20%   | Indicators (VWAP / EMA20/50/200) close to level |
| Persistence       |  10%   | Minutes the level has stood without breaking    |
| Rejection quality |   5%   | Avg wick/body ratio of touching bars            |

Each component is normalized to 0-100 before weighting so the
composite is also 0-100. Strategy uses the
`sr_strong_threshold` / `sr_weak_threshold` config knobs to gate.

All functions are pure on a `Sequence[CompletedBar]` (the 1-minute
buffer for the lookback window) plus an `IndicatorSnapshot` that the
caller computed once per detector tick.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from tradingbot.data.bars import CompletedBar

# --------------------------------------------------------------------------
# Tunables
# --------------------------------------------------------------------------

# 100 score saturation thresholds; chosen so a "good" level easily hits 100
# on each axis without being trivial. Override in the strategy layer if
# the operator wants to retune.
TOUCHES_SATURATION: int = 5
VOLUME_FRACTION_SATURATION: Decimal = Decimal("0.05")  # 5% of session vol
PERSISTENCE_SATURATION_MINUTES: int = 60
REJECTION_RATIO_SATURATION: Decimal = Decimal("2.0")
MA_CONFLUENCE_BAND_PCT: Decimal = Decimal("0.1")  # 0.1% of level price

# Body of 0 (doji) would divide by zero in the rejection score; clamp.
_BODY_EPS: Decimal = Decimal("0.0001")
_HUNDRED: Decimal = Decimal(100)


# --------------------------------------------------------------------------
# Data classes
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class StrengthComponents:
    """The five component scores, each 0-100."""

    clean_touches: Decimal
    volume_at_price: Decimal
    ma_confluence: Decimal
    persistence: Decimal
    rejection_quality: Decimal


@dataclass(frozen=True)
class StrengthWeights:
    """Weights for the five components. Caller is expected to ensure they sum to ~1."""

    clean_touches: Decimal
    volume_at_price: Decimal
    ma_confluence: Decimal
    persistence: Decimal
    rejection_quality: Decimal


DEFAULT_WEIGHTS = StrengthWeights(
    clean_touches=Decimal("0.35"),
    volume_at_price=Decimal("0.30"),
    ma_confluence=Decimal("0.20"),
    persistence=Decimal("0.10"),
    rejection_quality=Decimal("0.05"),
)


@dataclass(frozen=True)
class IndicatorSnapshot:
    """Indicator values for the strength scorer.

    The detector computes these once per tick from the 1m buffer and
    passes them by value. Any value that hasn't warmed up yet stays
    `None` and contributes 0 to MA confluence.
    """

    vwap: Decimal | None = None
    ema20: Decimal | None = None
    ema50: Decimal | None = None
    ema200: Decimal | None = None


# --------------------------------------------------------------------------
# Components
# --------------------------------------------------------------------------


def clean_touches_score(
    bars: Sequence[CompletedBar],
    level: Decimal,
    tolerance_pct: Decimal,
) -> Decimal:
    """Count bars whose H–L range intersects [level ± tol]. Saturates at 5 -> 100."""
    if level <= 0 or not bars:
        return Decimal(0)
    tol = level * tolerance_pct / _HUNDRED
    lo = level - tol
    hi = level + tol
    touches = sum(1 for bar in bars if bar.low <= hi and bar.high >= lo)
    scaled = Decimal(touches) / Decimal(TOUCHES_SATURATION)
    return min(scaled, Decimal(1)) * _HUNDRED


def volume_at_price_score(
    bars: Sequence[CompletedBar],
    level: Decimal,
    tolerance_pct: Decimal,
) -> Decimal:
    """Fraction of session volume inside [level ± tol]. 5% of total -> 100."""
    if level <= 0 or not bars:
        return Decimal(0)
    tol = level * tolerance_pct / _HUNDRED
    lo = level - tol
    hi = level + tol

    total = sum((bar.volume for bar in bars), Decimal(0))
    if total == 0:
        return Decimal(0)

    volume_at = Decimal(0)
    for bar in bars:
        if bar.high == bar.low:
            if lo <= bar.low <= hi:
                volume_at += bar.volume
            continue
        overlap_lo = max(lo, bar.low)
        overlap_hi = min(hi, bar.high)
        if overlap_hi > overlap_lo:
            fraction = (overlap_hi - overlap_lo) / (bar.high - bar.low)
            volume_at += bar.volume * fraction

    raw_fraction = volume_at / total
    scaled = raw_fraction / VOLUME_FRACTION_SATURATION
    return min(scaled, Decimal(1)) * _HUNDRED


def ma_confluence_score(
    level: Decimal,
    indicators: IndicatorSnapshot,
    *,
    band_pct: Decimal = MA_CONFLUENCE_BAND_PCT,
) -> Decimal:
    """+25 per indicator (VWAP, EMA20, EMA50, EMA200) within ±band_pct of level."""
    if level <= 0:
        return Decimal(0)
    score = Decimal(0)
    for value in (
        indicators.vwap,
        indicators.ema20,
        indicators.ema50,
        indicators.ema200,
    ):
        if value is None or value <= 0:
            continue
        diff_pct = abs(value - level) / level * _HUNDRED
        if diff_pct <= band_pct:
            score += Decimal(25)
    return min(score, _HUNDRED)


def persistence_score(
    bars: Sequence[CompletedBar],
    level: Decimal,
    tolerance_pct: Decimal,
    minutes_per_bar: int,
) -> Decimal:
    """Minutes since the most recent definitive break of the level.

    A bar "breaks" when its full H–L range lies on one side of the
    band [level ± tol] (i.e. price moved past the level cleanly).
    The score is `min(minutes_held / 60, 1) * 100`.
    """
    if level <= 0 or not bars or minutes_per_bar < 1:
        return Decimal(0)
    tol = level * tolerance_pct / _HUNDRED
    lo = level - tol
    hi = level + tol

    bars_held = 0
    for bar in reversed(bars):
        if bar.high < lo or bar.low > hi:
            break
        bars_held += 1

    minutes = bars_held * minutes_per_bar
    scaled = Decimal(minutes) / Decimal(PERSISTENCE_SATURATION_MINUTES)
    return min(scaled, Decimal(1)) * _HUNDRED


def rejection_quality_score(
    bars: Sequence[CompletedBar],
    level: Decimal,
    tolerance_pct: Decimal,
) -> Decimal:
    """Average wick/body ratio of bars that touched the level.

    For each touching bar:
    - Upper wick (if high is in band): `high - max(open, close)`
    - Lower wick (if low is in band): `min(open, close) - low`

    Body = `|close - open|`; clamped to `_BODY_EPS` to avoid /0
    (a doji at the level is a strong rejection signal anyway). The
    ratio is normalized so 2.0 -> score 100.
    """
    if level <= 0 or not bars:
        return Decimal(0)
    tol = level * tolerance_pct / _HUNDRED
    lo = level - tol
    hi = level + tol

    ratios: list[Decimal] = []
    for bar in bars:
        body = max(abs(bar.close - bar.open), _BODY_EPS)
        if lo <= bar.high <= hi:
            wick = bar.high - max(bar.open, bar.close)
            if wick > 0:
                ratios.append(wick / body)
        if lo <= bar.low <= hi:
            wick = min(bar.open, bar.close) - bar.low
            if wick > 0:
                ratios.append(wick / body)

    if not ratios:
        return Decimal(0)
    avg = sum(ratios, Decimal(0)) / Decimal(len(ratios))
    scaled = avg / REJECTION_RATIO_SATURATION
    return min(scaled, Decimal(1)) * _HUNDRED


def compute_strength(
    level: Decimal,
    bars: Sequence[CompletedBar],
    indicators: IndicatorSnapshot,
    *,
    tolerance_pct: Decimal,
    minutes_per_bar: int,
    weights: StrengthWeights = DEFAULT_WEIGHTS,
) -> tuple[Decimal, StrengthComponents]:
    """Composite 0-100 strength score with the five components broken out."""
    c1 = clean_touches_score(bars, level, tolerance_pct)
    c2 = volume_at_price_score(bars, level, tolerance_pct)
    c3 = ma_confluence_score(level, indicators)
    c4 = persistence_score(bars, level, tolerance_pct, minutes_per_bar)
    c5 = rejection_quality_score(bars, level, tolerance_pct)
    components = StrengthComponents(c1, c2, c3, c4, c5)
    strength = (
        weights.clean_touches * c1
        + weights.volume_at_price * c2
        + weights.ma_confluence * c3
        + weights.persistence * c4
        + weights.rejection_quality * c5
    )
    return strength, components
