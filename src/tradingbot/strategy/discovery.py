"""Discovery strategy: produce one `TradingSignal | None` per evaluation.

CLAUDE.md §6:
- Approaching a resistance from BELOW with a rejection (bear bar
  closing back below the level) -> SHORT.
- Approaching a support from ABOVE with a rebound (bull bar closing
  back above the level) -> LONG.
- Strength ≥ strong threshold -> 100% of the size.
- weak ≤ strength < strong -> 50% partial; the strategy engine
  may issue a second tranche if price retests the level.
- Strength < weak threshold -> skip.

The trend-change trigger (rejection / rebound) reads the last
1-minute bar, in line with the user's clarification that 1-minute
bars drive entries once a 5m+15m-confirmed level exists.

Pure function: takes detected levels + the recent 1m series + the
config, returns a `TradingSignal` or `None`. The strategy engine
runs the side-effects (DB writes, risk manager, order router).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from tradingbot.data.bars import CompletedBar
from tradingbot.data.sr_detector import DetectedLevel
from tradingbot.persistence.enums import OrderSide, SRKind
from tradingbot.strategy.sizing import (
    SizingConfig,
    SizingRefusal,
    SizingResult,
    size_trade,
)
from tradingbot.strategy.types import TradingSignal

_HUNDRED: Decimal = Decimal(100)


@dataclass(frozen=True)
class DiscoveryConfig:
    """Trigger + sizing thresholds. Mirrors a subset of config_policies."""

    strong_threshold: Decimal
    weak_threshold: Decimal
    partial_entry_pct: Decimal
    proximity_tolerance_pct: Decimal  # how close current price must be to the level
    rejection_tolerance_pct: Decimal  # how close bar high/low must reach the level
    sizing: SizingConfig


def is_rejection_bar(
    bar: CompletedBar, level_price: Decimal, tolerance_pct: Decimal
) -> bool:
    """Bear bar that pierced (or grazed) the resistance and closed below.

    Conditions:
    - `bar.high` reaches at least `level_price * (1 - tolerance_pct/100)`.
    - `bar.close < bar.open` (bear body).
    - `bar.close < level_price`.
    """
    if level_price <= 0:
        return False
    reach = level_price * (Decimal(1) - tolerance_pct / _HUNDRED)
    return bar.high >= reach and bar.close < bar.open and bar.close < level_price


def is_rebound_bar(
    bar: CompletedBar, level_price: Decimal, tolerance_pct: Decimal
) -> bool:
    """Bull bar that pierced (or grazed) the support and closed above."""
    if level_price <= 0:
        return False
    reach = level_price * (Decimal(1) + tolerance_pct / _HUNDRED)
    return bar.low <= reach and bar.close > bar.open and bar.close > level_price


def _next_resistance_above(
    levels: Sequence[DetectedLevel], current_price: Decimal
) -> Decimal | None:
    above = [
        lvl
        for lvl in levels
        if lvl.kind == SRKind.RESISTANCE and lvl.price > current_price
    ]
    if not above:
        return None
    return min(above, key=lambda lvl: lvl.price).price


def _next_support_below(
    levels: Sequence[DetectedLevel], current_price: Decimal
) -> Decimal | None:
    below = [
        lvl
        for lvl in levels
        if lvl.kind == SRKind.SUPPORT and lvl.price < current_price
    ]
    if not below:
        return None
    return max(below, key=lambda lvl: lvl.price).price


def _within_proximity(
    level_price: Decimal, current_price: Decimal, tolerance_pct: Decimal
) -> bool:
    if current_price <= 0:
        return False
    distance_pct = abs(level_price - current_price) / current_price * _HUNDRED
    return distance_pct <= tolerance_pct


def _scale_partial(result: SizingResult, partial_pct: Decimal) -> SizingResult | None:
    """Halve (or whatever pct) the sized order for a weak-signal entry.

    Returns None if the resulting qty rounds to less than 1 share.
    """
    fraction = partial_pct / _HUNDRED
    new_qty = (result.qty * fraction).to_integral_value(rounding="ROUND_DOWN")
    if new_qty < 1:
        return None
    new_profit = result.expected_profit_usd * new_qty / result.qty
    # Commission re-estimated at the smaller qty rather than scaled
    # linearly, because the $0.35 minimum-per-side dominates at low
    # share counts.
    from tradingbot.strategy.sizing import estimate_round_trip_commission

    return SizingResult(
        qty=new_qty,
        stop_loss_price=result.stop_loss_price,
        take_profit_price=result.take_profit_price,
        expected_profit_usd=new_profit,
        expected_commission_usd=estimate_round_trip_commission(new_qty),
        r_multiple=result.r_multiple,
    )


def evaluate_entry(
    levels: Sequence[DetectedLevel],
    bars_1m: Sequence[CompletedBar],
    config: DiscoveryConfig,
) -> TradingSignal | None:
    """Pick the strongest actionable level and emit a signal if it fires.

    Returns None when nothing meets the trigger. The strategy engine
    will call this on every tick of the discovery loop.
    """
    if not bars_1m or not levels:
        return None

    last_bar = bars_1m[-1]
    current_price = last_bar.close
    if current_price <= 0:
        return None

    # Strongest-first so a single emit serves the best available setup.
    actionable = sorted(
        (lvl for lvl in levels if lvl.strength >= config.weak_threshold),
        key=lambda lvl: lvl.strength,
        reverse=True,
    )
    if not actionable:
        return None

    for level in actionable:
        # Proximity is measured from the bar EXTREME that touches the
        # level (high for resistance, low for support), not from the
        # close — after a rejection / rebound the close has already
        # moved away.
        touch = (
            last_bar.high if level.kind == SRKind.RESISTANCE else last_bar.low
        )
        if not _within_proximity(
            level.price, touch, config.proximity_tolerance_pct
        ):
            continue

        if level.kind == SRKind.RESISTANCE:
            if current_price >= level.price:
                continue
            if not is_rejection_bar(
                last_bar, level.price, config.rejection_tolerance_pct
            ):
                continue
            side = OrderSide.SELL
            target = _next_support_below(levels, current_price)
        else:
            if current_price <= level.price:
                continue
            if not is_rebound_bar(
                last_bar, level.price, config.rejection_tolerance_pct
            ):
                continue
            side = OrderSide.BUY
            target = _next_resistance_above(levels, current_price)

        if target is None:
            continue

        sized = size_trade(
            entry_price=current_price,
            target_price=target,
            side=side,
            config=config.sizing,
        )
        if isinstance(sized, SizingRefusal):
            continue

        is_partial = level.strength < config.strong_threshold
        if is_partial:
            partial = _scale_partial(sized, config.partial_entry_pct)
            if partial is None:
                continue
            sized = partial

        return TradingSignal(
            symbol=level.symbol,
            side=side,
            entry_price=current_price,
            stop_loss_price=sized.stop_loss_price,
            take_profit_price=sized.take_profit_price,
            qty=sized.qty,
            expected_profit_usd=sized.expected_profit_usd,
            expected_commission_usd=sized.expected_commission_usd,
            r_multiple=sized.r_multiple,
            # DetectedLevel does not carry the DB id; the engine looks
            # it up via SRLevelRepository and stamps it before persist.
            sr_level_id=None,
            sr_level_strength=level.strength,
            is_partial=is_partial,
        )

    return None
