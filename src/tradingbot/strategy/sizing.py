"""Per-trade sizing math (CLAUDE.md §6).

`size_trade` is the canonical implementation of the eight-step
algorithm described in CLAUDE.md. Given an entry price and a target
level, it returns either:
  - a `SizingResult` with concrete qty / stop / take-profit / expected
    profit + commission, or
  - a `SizingRefusal` with a human-readable reason.

The function is pure. Property tests pin its invariants.

IBKR Pro commission is tiered: `$0.0035/share, $0.35 min, max 1% of
trade value` (the 1% cap is not modeled — it only applies on very
low-priced shares and is negligible compared to the rest of the
filters).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from tradingbot.persistence.enums import OrderSide

# IBKR Pro tiered defaults. CLAUDE.md §2 principle 5 forbids reducing
# these in cost calculations without owner approval.
IBKR_COMMISSION_PER_SHARE: Decimal = Decimal("0.0035")
IBKR_COMMISSION_MIN_PER_SIDE: Decimal = Decimal("0.35")

_HUNDRED: Decimal = Decimal(100)


@dataclass(frozen=True)
class SizingConfig:
    """Inputs to the sizing math from the active config policy."""

    stop_loss_pct: Decimal  # percent units (0.5 means 0.5%)
    min_profit_per_trade_usd: Decimal
    max_profit_per_trade_usd: Decimal
    min_r_multiple: Decimal
    max_commission_pct_of_target: Decimal  # percent (5 means 5%)
    max_position_size_usd: Decimal


@dataclass(frozen=True)
class SizingResult:
    """A successful sizing decision, ready to feed the risk manager."""

    qty: Decimal
    stop_loss_price: Decimal
    take_profit_price: Decimal
    expected_profit_usd: Decimal
    expected_commission_usd: Decimal
    r_multiple: Decimal


@dataclass(frozen=True)
class SizingRefusal:
    """A documented "do not enter" outcome. Carries the reason for logs."""

    reason: str


def estimate_round_trip_commission(qty: Decimal) -> Decimal:
    """IBKR Pro tiered, both legs combined.

    Per side: `max(qty * 0.0035, 0.35)`.
    """
    per_side = max(qty * IBKR_COMMISSION_PER_SHARE, IBKR_COMMISSION_MIN_PER_SIDE)
    return per_side * Decimal(2)


def size_trade(
    *,
    entry_price: Decimal,
    target_price: Decimal,
    side: OrderSide,
    config: SizingConfig,
) -> SizingResult | SizingRefusal:
    """Compute the trade per CLAUDE.md §6, steps 1-8.

    Step numbers in the body match the spec verbatim so the
    correspondence is auditable.
    """
    if entry_price <= 0:
        return SizingRefusal(reason=f"entry_price {entry_price} must be > 0")
    if target_price <= 0:
        return SizingRefusal(reason=f"target_price {target_price} must be > 0")

    # 1-2. target_pct on the correct side of entry.
    if side == OrderSide.BUY and target_price <= entry_price:
        return SizingRefusal(
            reason=f"long target {target_price} must be > entry {entry_price}"
        )
    if side == OrderSide.SELL and target_price >= entry_price:
        return SizingRefusal(
            reason=f"short target {target_price} must be < entry {entry_price}"
        )
    target_pct = abs(target_price - entry_price) / entry_price * _HUNDRED

    # 3. SL on the opposite side of entry from the target.
    if side == OrderSide.BUY:
        stop_loss_price = entry_price * (
            Decimal(1) - config.stop_loss_pct / _HUNDRED
        )
    else:
        stop_loss_price = entry_price * (
            Decimal(1) + config.stop_loss_pct / _HUNDRED
        )

    # 4-5. R-multiple filter.
    if config.stop_loss_pct <= 0:
        return SizingRefusal(reason="stop_loss_pct must be > 0")
    r_multiple = target_pct / config.stop_loss_pct
    if r_multiple < config.min_r_multiple:
        return SizingRefusal(
            reason=(
                f"r_multiple {r_multiple} < min {config.min_r_multiple} "
                f"(target_pct={target_pct}, stop_pct={config.stop_loss_pct})"
            )
        )

    # 6. Notional size bounds.
    #    profit_usd = (target_pct / 100) * notional
    #    notional   = profit_usd / (target_pct / 100)
    target_fraction = target_pct / _HUNDRED
    min_size_usd = config.min_profit_per_trade_usd / target_fraction
    max_size_usd = config.max_profit_per_trade_usd / target_fraction

    # 7. Choose position_size = min(max_size, hard cap). Reject if below min.
    position_size_usd = min(max_size_usd, config.max_position_size_usd)
    if position_size_usd < min_size_usd:
        return SizingRefusal(
            reason=(
                f"position_size_usd {position_size_usd} < min_size_usd "
                f"{min_size_usd} (target_pct={target_pct})"
            )
        )

    raw_qty = position_size_usd / entry_price
    # Whole shares only for US equities. Round DOWN to be safe (never
    # exceed max_position_size_usd).
    qty = raw_qty.to_integral_value(rounding="ROUND_DOWN")
    if qty < 1:
        return SizingRefusal(reason=f"qty {qty} < 1 share")

    actual_profit = abs(target_price - entry_price) * qty

    # 8. Commission filter.
    commission = estimate_round_trip_commission(qty)
    if actual_profit <= 0:
        return SizingRefusal(reason="expected profit is zero")
    commission_pct = commission / actual_profit * _HUNDRED
    if commission_pct > config.max_commission_pct_of_target:
        return SizingRefusal(
            reason=(
                f"commission {commission} ({commission_pct}% of profit) "
                f"exceeds max {config.max_commission_pct_of_target}%"
            )
        )

    return SizingResult(
        qty=qty,
        stop_loss_price=stop_loss_price,
        take_profit_price=target_price,
        expected_profit_usd=actual_profit,
        expected_commission_usd=commission,
        r_multiple=r_multiple,
    )
