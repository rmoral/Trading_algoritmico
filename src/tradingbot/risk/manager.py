"""`RiskManager.approve()`: the only gate before order submission.

CLAUDE.md §2 principle 2 says every order MUST pass through this
function; there is no fast path. The implementation is pure
(synchronous, side-effect-free) so it is trivial to property-test
with Hypothesis — and Hypothesis is exactly what CLAUDE.md §9
requires for any change touching this module.

The manager runs every check on every call so the decision lists
all reasons at once (instead of short-circuiting on the first).
Soft limits (e.g. "approaching the daily loss cap") emit warnings
that DO NOT block the trade.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from tradingbot.logging_setup import get_logger
from tradingbot.persistence.enums import OrderSide
from tradingbot.risk.types import (
    OrderRequest,
    RiskContext,
    RiskDecision,
    RiskLimits,
    RiskRefusalReason,
)

# Soft-limit thresholds. The strategy can surface these.
_SOFT_DAILY_LOSS_PCT: Decimal = Decimal("0.80")  # warn at 80% of cap
_SOFT_TRADES_PCT: Decimal = Decimal("0.80")
_HUNDRED: Decimal = Decimal(100)
_TEN_THOUSAND: Decimal = Decimal(10000)


class RiskManager:
    """Pure approval gate. Build once per process; reuse for every order."""

    def __init__(self, limits: RiskLimits) -> None:
        self._limits = limits
        self._log = get_logger(__name__)

    @property
    def limits(self) -> RiskLimits:
        return self._limits

    def approve(self, request: OrderRequest, ctx: RiskContext) -> RiskDecision:
        refusals: list[RiskRefusalReason] = []
        warnings: list[str] = []

        # ---- structural validation ----
        if not _order_is_well_formed(request):
            refusals.append(RiskRefusalReason.INVALID_ORDER)
            # Short-circuit further checks that depend on prices being
            # in a sane configuration.
            decision = RiskDecision(
                refusals=tuple(refusals), warnings=tuple(warnings)
            )
            self._emit(request, decision)
            return decision

        # ---- absolute kills ----
        if ctx.kill_switch_tripped:
            refusals.append(RiskRefusalReason.KILL_SWITCH)
        if ctx.has_open_position:
            refusals.append(RiskRefusalReason.HAS_OPEN_POSITION)
        if request.symbol in self._limits.forbidden_tickers:
            refusals.append(RiskRefusalReason.FORBIDDEN_TICKER)
        if self._limits.earnings_blackout and ctx.is_earnings_day:
            refusals.append(RiskRefusalReason.EARNINGS_BLACKOUT)
        if _halt_cooldown_active(ctx, self._limits.halt_resume_cooldown_seconds):
            refusals.append(RiskRefusalReason.HALT_COOLDOWN)

        # ---- circuit breaker ----
        if ctx.consecutive_losses >= self._limits.consecutive_losses_limit:
            refusals.append(RiskRefusalReason.CONSECUTIVE_LOSSES)
        if ctx.drawdown_pct_from_open >= self._limits.drawdown_pct_from_open:
            refusals.append(RiskRefusalReason.DRAWDOWN_EXCEEDED)

        # ---- daily and rate caps ----
        if ctx.daily_loss_usd >= self._limits.max_daily_loss_usd:
            refusals.append(RiskRefusalReason.DAILY_LOSS_CAP)
        elif (
            ctx.daily_loss_usd
            >= self._limits.max_daily_loss_usd * _SOFT_DAILY_LOSS_PCT
        ):
            warnings.append(
                f"daily_loss_approaching: {ctx.daily_loss_usd} / "
                f"{self._limits.max_daily_loss_usd}"
            )

        if ctx.trades_today >= self._limits.max_trades_per_day:
            refusals.append(RiskRefusalReason.MAX_TRADES_PER_DAY)
        elif (
            Decimal(ctx.trades_today)
            >= Decimal(self._limits.max_trades_per_day) * _SOFT_TRADES_PCT
        ):
            warnings.append(
                f"trades_count_approaching: {ctx.trades_today} / "
                f"{self._limits.max_trades_per_day}"
            )

        if ctx.recent_orders_per_minute >= self._limits.max_orders_per_minute:
            refusals.append(RiskRefusalReason.MAX_ORDERS_PER_MINUTE)

        # ---- per-trade sizing and risk-reward ----
        notional = request.entry_price * request.qty
        if notional > self._limits.max_position_size_usd:
            refusals.append(RiskRefusalReason.POSITION_SIZE_CAP)

        stop_pct = _stop_loss_pct(request)
        if stop_pct > self._limits.stop_loss_pct:
            refusals.append(RiskRefusalReason.STOP_LOSS_PCT_EXCEEDED)

        r_multiple = _r_multiple(request)
        if r_multiple < self._limits.min_r_multiple:
            refusals.append(RiskRefusalReason.R_MULTIPLE_TOO_LOW)

        expected_profit = _expected_profit_usd(request)
        if expected_profit > 0:
            commission_pct = (
                request.estimated_commission_usd / expected_profit * _HUNDRED
            )
            if commission_pct > self._limits.max_commission_pct_of_target:
                refusals.append(RiskRefusalReason.COMMISSION_TOO_HIGH)

        # ---- microstructure ----
        spread_bps = _spread_bps(request)
        if spread_bps is not None:
            if spread_bps < self._limits.min_spread_bps:
                refusals.append(RiskRefusalReason.SPREAD_TOO_NARROW)
            elif spread_bps > self._limits.max_spread_bps:
                refusals.append(RiskRefusalReason.SPREAD_TOO_WIDE)

        decision = RiskDecision(refusals=tuple(refusals), warnings=tuple(warnings))
        self._emit(request, decision)
        return decision

    def _emit(self, request: OrderRequest, decision: RiskDecision) -> None:
        if decision.approved:
            self._log.info(
                "risk_approved",
                symbol=request.symbol,
                side=request.side.value,
                qty=str(request.qty),
                warnings=list(decision.warnings),
            )
        else:
            self._log.warning(
                "risk_refused",
                symbol=request.symbol,
                side=request.side.value,
                qty=str(request.qty),
                refusals=[r.value for r in decision.refusals],
                warnings=list(decision.warnings),
            )


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _order_is_well_formed(request: OrderRequest) -> bool:
    """Sanity: prices positive, stop/target on the correct sides of entry."""
    if request.qty <= 0:
        return False
    if request.entry_price <= 0:
        return False
    if request.stop_loss_price <= 0:
        return False
    if request.take_profit_price <= 0:
        return False
    if request.estimated_commission_usd < 0:
        return False
    if request.side == OrderSide.BUY:
        if not (request.stop_loss_price < request.entry_price < request.take_profit_price):
            return False
    else:  # SELL / short
        if not (request.take_profit_price < request.entry_price < request.stop_loss_price):
            return False
    return True


def _stop_loss_pct(request: OrderRequest) -> Decimal:
    """Distance from entry to stop, as a percentage of entry."""
    return (
        abs(request.entry_price - request.stop_loss_price)
        / request.entry_price
        * _HUNDRED
    )


def _r_multiple(request: OrderRequest) -> Decimal:
    """target distance / stop distance, both in absolute price terms."""
    stop_distance = abs(request.entry_price - request.stop_loss_price)
    if stop_distance == 0:
        return Decimal(0)
    target_distance = abs(request.take_profit_price - request.entry_price)
    return target_distance / stop_distance


def _expected_profit_usd(request: OrderRequest) -> Decimal:
    """Gross USD profit if the take-profit fills exactly. >= 0."""
    return abs(request.take_profit_price - request.entry_price) * request.qty


def _spread_bps(request: OrderRequest) -> Decimal | None:
    """Quoted spread in basis points, or None when bid/ask are missing.

    bps = (ask - bid) / midpoint * 10_000.
    Crossed quotes (ask <= bid) return 0.
    """
    if request.bid is None or request.ask is None:
        return None
    if request.bid <= 0 or request.ask <= 0:
        return None
    if request.ask <= request.bid:
        return Decimal(0)
    midpoint = (request.bid + request.ask) / Decimal(2)
    if midpoint == 0:
        return None
    return (request.ask - request.bid) / midpoint * _TEN_THOUSAND


def _halt_cooldown_active(ctx: RiskContext, cooldown_seconds: int) -> bool:
    """True while we are still in the post-halt cooldown window."""
    if ctx.halt_active:
        return True
    if ctx.halt_resumed_at is None:
        return False
    elapsed = ctx.now - ctx.halt_resumed_at
    return elapsed < timedelta(seconds=cooldown_seconds)


__all__ = ["RiskManager"]
