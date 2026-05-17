"""Pivot detection + multi-timeframe confirmation.

Pure functions only. The S/R detector applies these to the rolling
5-minute and 15-minute buffers maintained by `MarketDataService`,
keeps only the levels that show up in both timeframes (within
`sr_level_tolerance_pct`), and hands them to the strength scorer in
`sr_strength.py`.

A "pivot high" is a bar whose high is a STRICT local maximum over a
±`window` window — ties do not count. Mirror condition for pivot
lows. Strict inequality keeps flat plateaus from producing spurious
pivots on every bar.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from tradingbot.data.bars import CompletedBar
from tradingbot.persistence.enums import BarResolution, SRKind


@dataclass(frozen=True)
class Pivot:
    """A local-extremum bar from one timeframe."""

    timeframe: BarResolution
    ts: datetime
    price: Decimal
    kind: SRKind  # RESISTANCE for pivot highs, SUPPORT for pivot lows


@dataclass(frozen=True)
class ConfirmedLevel:
    """A 15m pivot whose price is matched by a 5m pivot of the same kind.

    `price` carries the 15m pivot's price: the higher timeframe is
    treated as authoritative. The 5m pivot is retained as evidence
    and made available to the strength scorer for the persistence
    component.
    """

    kind: SRKind
    price: Decimal
    pivot_15m: Pivot
    pivot_5m: Pivot


def find_pivots(
    bars: Sequence[CompletedBar],
    *,
    window: int,
    timeframe: BarResolution,
) -> list[Pivot]:
    """Strict-extremum pivots over a ±`window` window.

    Returns pivots in chronological order. `window` must be >= 1.
    Returns `[]` when fewer than `2 * window + 1` bars are available
    (no candidate position has full neighbourhoods on both sides).
    """
    if window < 1:
        raise ValueError(f"window must be >= 1; got {window}")
    n = len(bars)
    if n < 2 * window + 1:
        return []

    pivots: list[Pivot] = []
    for i in range(window, n - window):
        h_i = bars[i].high
        l_i = bars[i].low
        is_high = True
        is_low = True
        for j in range(i - window, i + window + 1):
            if j == i:
                continue
            if bars[j].high >= h_i:
                is_high = False
            if bars[j].low <= l_i:
                is_low = False
            if not is_high and not is_low:
                break
        if is_high:
            pivots.append(
                Pivot(
                    timeframe=timeframe,
                    ts=bars[i].ts,
                    price=h_i,
                    kind=SRKind.RESISTANCE,
                )
            )
        if is_low:
            pivots.append(
                Pivot(
                    timeframe=timeframe,
                    ts=bars[i].ts,
                    price=l_i,
                    kind=SRKind.SUPPORT,
                )
            )
    return pivots


def cross_confirm(
    pivots_15m: Sequence[Pivot],
    pivots_5m: Sequence[Pivot],
    *,
    tolerance_pct: Decimal,
) -> list[ConfirmedLevel]:
    """Pair each 15m pivot with the closest same-kind 5m pivot within tolerance.

    Match condition (in percent of the 15m pivot price):
        100 * |p15.price - p5.price| / p15.price  <=  tolerance_pct

    Each 15m pivot yields at most one `ConfirmedLevel` (the closest
    matching 5m pivot wins). The same 5m pivot may confirm multiple
    15m pivots — the higher timeframe drives the level set.

    The returned list is in the same chronological order as the
    input 15m pivots.
    """
    confirmed: list[ConfirmedLevel] = []
    hundred = Decimal(100)
    for p15 in pivots_15m:
        if p15.price <= 0:
            continue
        best: tuple[Decimal, Pivot] | None = None
        for p5 in pivots_5m:
            if p5.kind != p15.kind:
                continue
            diff_pct = abs(p15.price - p5.price) / p15.price * hundred
            if diff_pct <= tolerance_pct and (best is None or diff_pct < best[0]):
                best = (diff_pct, p5)
        if best is not None:
            confirmed.append(
                ConfirmedLevel(
                    kind=p15.kind,
                    price=p15.price,
                    pivot_15m=p15,
                    pivot_5m=best[1],
                )
            )
    return confirmed
