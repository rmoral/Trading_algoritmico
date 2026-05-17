"""Deterministic and Hypothesis-based tests for `RiskManager`.

CLAUDE.md §9 requires property-based tests on every change here.
Each refusal reason gets a deterministic test that pins the
trigger condition, plus the module-level property tests assert
always-true invariants like "kill switch tripped -> refused" no
matter what the rest of the inputs look like.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tradingbot.persistence.enums import OrderSide, OrderType
from tradingbot.risk import (
    OrderRequest,
    RiskContext,
    RiskLimits,
    RiskManager,
    RiskRefusalReason,
)

# ---------- helpers ----------


def _limits(**overrides: object) -> RiskLimits:
    base: dict[str, object] = {
        "max_position_size_usd": Decimal("50000"),
        "stop_loss_pct": Decimal("0.5"),
        "min_r_multiple": Decimal("1.5"),
        "max_commission_pct_of_target": Decimal("5"),
        "max_daily_loss_usd": Decimal("2250"),
        "max_trades_per_day": 50,
        "max_orders_per_minute": 30,
        "min_spread_bps": 0,
        "max_spread_bps": 20,
        "forbidden_tickers": (),
        "earnings_blackout": True,
        "halt_resume_cooldown_seconds": 60,
        "consecutive_losses_limit": 5,
        "drawdown_pct_from_open": Decimal("1.5"),
    }
    base.update(overrides)
    return RiskLimits(**base)  # type: ignore[arg-type]


def _context(**overrides: object) -> RiskContext:
    base: dict[str, object] = {
        "kill_switch_tripped": False,
        "has_open_position": False,
        "daily_loss_usd": Decimal("0"),
        "trades_today": 0,
        "recent_orders_per_minute": 0,
        "consecutive_losses": 0,
        "drawdown_pct_from_open": Decimal("0"),
        "is_earnings_day": False,
        "halt_active": False,
        "halt_resumed_at": None,
        "now": datetime(2026, 5, 16, 14, 30, tzinfo=UTC),
    }
    base.update(overrides)
    return RiskContext(**base)  # type: ignore[arg-type]


def _long_request(**overrides: object) -> OrderRequest:
    """A clean long order that passes every filter under the defaults.

    Entry 100, qty 250 -> notional 25_000 (< 50_000 cap).
    Stop at 99.5 (-0.5%), target at 101 (+1%) -> R=2 (>= 1.5).
    Profit if hit = 250 (gross). Commission $1.75 (~0.7% < 5%).
    Spread 1 bps (between 0 and 20).
    """
    base: dict[str, object] = {
        "symbol": "AAPL",
        "side": OrderSide.BUY,
        "qty": Decimal("250"),
        "order_type": OrderType.LIMIT,
        "entry_price": Decimal("100"),
        "stop_loss_price": Decimal("99.5"),
        "take_profit_price": Decimal("101"),
        "estimated_commission_usd": Decimal("1.75"),
        "bid": Decimal("99.995"),
        "ask": Decimal("100.005"),
    }
    base.update(overrides)
    return OrderRequest(**base)  # type: ignore[arg-type]


# ---------- happy path ----------


def test_approves_clean_request() -> None:
    rm = RiskManager(_limits())
    decision = rm.approve(_long_request(), _context())
    assert decision.approved
    assert decision.refusals == ()


# ---------- structural ----------


def test_negative_qty_invalid() -> None:
    rm = RiskManager(_limits())
    decision = rm.approve(_long_request(qty=Decimal("-1")), _context())
    assert RiskRefusalReason.INVALID_ORDER in decision.refusals


def test_long_with_inverted_stop_invalid() -> None:
    """Long order whose stop is ABOVE entry is malformed."""
    rm = RiskManager(_limits())
    decision = rm.approve(
        _long_request(stop_loss_price=Decimal("101")),
        _context(),
    )
    assert RiskRefusalReason.INVALID_ORDER in decision.refusals


def test_short_request_well_formed() -> None:
    rm = RiskManager(_limits())
    short = _long_request(
        side=OrderSide.SELL,
        entry_price=Decimal("100"),
        stop_loss_price=Decimal("100.5"),  # above entry for shorts
        take_profit_price=Decimal("99"),  # below entry for shorts
    )
    decision = rm.approve(short, _context())
    assert decision.approved


# ---------- absolute kills ----------


def test_kill_switch_refuses() -> None:
    rm = RiskManager(_limits())
    decision = rm.approve(_long_request(), _context(kill_switch_tripped=True))
    assert RiskRefusalReason.KILL_SWITCH in decision.refusals


def test_open_position_refuses() -> None:
    rm = RiskManager(_limits())
    decision = rm.approve(_long_request(), _context(has_open_position=True))
    assert RiskRefusalReason.HAS_OPEN_POSITION in decision.refusals


def test_forbidden_ticker_refuses() -> None:
    rm = RiskManager(_limits(forbidden_tickers=("AAPL",)))
    decision = rm.approve(_long_request(), _context())
    assert RiskRefusalReason.FORBIDDEN_TICKER in decision.refusals


def test_earnings_blackout_refuses() -> None:
    rm = RiskManager(_limits(earnings_blackout=True))
    decision = rm.approve(_long_request(), _context(is_earnings_day=True))
    assert RiskRefusalReason.EARNINGS_BLACKOUT in decision.refusals


def test_earnings_blackout_disabled_allows() -> None:
    rm = RiskManager(_limits(earnings_blackout=False))
    decision = rm.approve(_long_request(), _context(is_earnings_day=True))
    assert RiskRefusalReason.EARNINGS_BLACKOUT not in decision.refusals


# ---------- halts ----------


def test_active_halt_refuses() -> None:
    rm = RiskManager(_limits())
    decision = rm.approve(_long_request(), _context(halt_active=True))
    assert RiskRefusalReason.HALT_COOLDOWN in decision.refusals


def test_recent_halt_resume_refuses() -> None:
    now = datetime(2026, 5, 16, 14, 30, tzinfo=UTC)
    rm = RiskManager(_limits(halt_resume_cooldown_seconds=60))
    decision = rm.approve(
        _long_request(),
        _context(now=now, halt_resumed_at=now - timedelta(seconds=30)),
    )
    assert RiskRefusalReason.HALT_COOLDOWN in decision.refusals


def test_halt_cooldown_elapsed_allows() -> None:
    now = datetime(2026, 5, 16, 14, 30, tzinfo=UTC)
    rm = RiskManager(_limits(halt_resume_cooldown_seconds=60))
    decision = rm.approve(
        _long_request(),
        _context(now=now, halt_resumed_at=now - timedelta(seconds=61)),
    )
    assert RiskRefusalReason.HALT_COOLDOWN not in decision.refusals


# ---------- circuit breaker ----------


def test_consecutive_losses_at_limit_refuses() -> None:
    rm = RiskManager(_limits(consecutive_losses_limit=5))
    decision = rm.approve(_long_request(), _context(consecutive_losses=5))
    assert RiskRefusalReason.CONSECUTIVE_LOSSES in decision.refusals


def test_drawdown_exceeded_refuses() -> None:
    rm = RiskManager(_limits(drawdown_pct_from_open=Decimal("1.5")))
    decision = rm.approve(
        _long_request(), _context(drawdown_pct_from_open=Decimal("1.6"))
    )
    assert RiskRefusalReason.DRAWDOWN_EXCEEDED in decision.refusals


# ---------- daily caps and warnings ----------


def test_daily_loss_at_cap_refuses() -> None:
    rm = RiskManager(_limits(max_daily_loss_usd=Decimal("2250")))
    decision = rm.approve(
        _long_request(), _context(daily_loss_usd=Decimal("2250"))
    )
    assert RiskRefusalReason.DAILY_LOSS_CAP in decision.refusals


def test_daily_loss_approaching_warns_but_allows() -> None:
    rm = RiskManager(_limits(max_daily_loss_usd=Decimal("2250")))
    # 80% of 2250 = 1800.
    decision = rm.approve(
        _long_request(), _context(daily_loss_usd=Decimal("1900"))
    )
    assert RiskRefusalReason.DAILY_LOSS_CAP not in decision.refusals
    assert any("daily_loss_approaching" in w for w in decision.warnings)


def test_max_trades_per_day_refuses() -> None:
    rm = RiskManager(_limits(max_trades_per_day=50))
    decision = rm.approve(_long_request(), _context(trades_today=50))
    assert RiskRefusalReason.MAX_TRADES_PER_DAY in decision.refusals


def test_trades_count_approaching_warns() -> None:
    rm = RiskManager(_limits(max_trades_per_day=50))
    decision = rm.approve(_long_request(), _context(trades_today=42))
    assert RiskRefusalReason.MAX_TRADES_PER_DAY not in decision.refusals
    assert any("trades_count_approaching" in w for w in decision.warnings)


def test_max_orders_per_minute_refuses() -> None:
    rm = RiskManager(_limits(max_orders_per_minute=30))
    decision = rm.approve(_long_request(), _context(recent_orders_per_minute=30))
    assert RiskRefusalReason.MAX_ORDERS_PER_MINUTE in decision.refusals


# ---------- sizing and R/commission ----------


def test_position_size_exceeded_refuses() -> None:
    """Notional 100 * 600 = 60_000 > 50_000 cap."""
    rm = RiskManager(_limits(max_position_size_usd=Decimal("50000")))
    decision = rm.approve(_long_request(qty=Decimal("600")), _context())
    assert RiskRefusalReason.POSITION_SIZE_CAP in decision.refusals


def test_stop_loss_pct_exceeded_refuses() -> None:
    """Entry 100, stop 98.5 -> 1.5% > 0.5% configured cap."""
    rm = RiskManager(_limits(stop_loss_pct=Decimal("0.5")))
    decision = rm.approve(
        _long_request(stop_loss_price=Decimal("98.5")), _context()
    )
    assert RiskRefusalReason.STOP_LOSS_PCT_EXCEEDED in decision.refusals


def test_r_multiple_too_low_refuses() -> None:
    """Entry 100, stop 99.5 (0.5 risk), target 100.5 (0.5 reward) -> R=1.0 < 1.5."""
    rm = RiskManager(_limits(min_r_multiple=Decimal("1.5")))
    decision = rm.approve(
        _long_request(take_profit_price=Decimal("100.5")), _context()
    )
    assert RiskRefusalReason.R_MULTIPLE_TOO_LOW in decision.refusals


def test_commission_too_high_refuses() -> None:
    """Profit if target = (101 - 100) * 250 = 250. Commission > 12.5 means >5%."""
    rm = RiskManager(_limits(max_commission_pct_of_target=Decimal("5")))
    decision = rm.approve(
        _long_request(estimated_commission_usd=Decimal("20")), _context()
    )
    assert RiskRefusalReason.COMMISSION_TOO_HIGH in decision.refusals


# ---------- spread ----------


def test_spread_too_wide_refuses() -> None:
    rm = RiskManager(_limits(max_spread_bps=20))
    # bid 99.5 ask 100.5 -> spread = 1 / 100 = 100 bps
    decision = rm.approve(
        _long_request(bid=Decimal("99.5"), ask=Decimal("100.5")), _context()
    )
    assert RiskRefusalReason.SPREAD_TOO_WIDE in decision.refusals


def test_spread_too_narrow_refuses() -> None:
    rm = RiskManager(_limits(min_spread_bps=5))
    # bid 99.999 ask 100.001 -> ~0.2 bps < 5
    decision = rm.approve(
        _long_request(bid=Decimal("99.999"), ask=Decimal("100.001")), _context()
    )
    assert RiskRefusalReason.SPREAD_TOO_NARROW in decision.refusals


def test_missing_quotes_skip_spread_check() -> None:
    rm = RiskManager(_limits())
    decision = rm.approve(_long_request(bid=None, ask=None), _context())
    assert decision.approved


# ---------- all reasons accumulate ----------


def test_decision_accumulates_multiple_refusals() -> None:
    """Killed AND open position AND forbidden ticker: all three reported."""
    rm = RiskManager(_limits(forbidden_tickers=("AAPL",)))
    decision = rm.approve(
        _long_request(),
        _context(kill_switch_tripped=True, has_open_position=True),
    )
    assert RiskRefusalReason.KILL_SWITCH in decision.refusals
    assert RiskRefusalReason.HAS_OPEN_POSITION in decision.refusals
    assert RiskRefusalReason.FORBIDDEN_TICKER in decision.refusals


# ---------- property tests ----------


@st.composite
def _long_request_strategy(draw: st.DrawFn) -> OrderRequest:
    """A well-formed long order with risk-favouring defaults."""
    entry = draw(
        st.decimals(
            min_value=Decimal("10"),
            max_value=Decimal("500"),
            allow_nan=False,
            allow_infinity=False,
            places=2,
        )
    )
    # Stop within [0.1%, 1.5%] below entry.
    stop_pct = draw(
        st.decimals(
            min_value=Decimal("0.1"),
            max_value=Decimal("1.5"),
            allow_nan=False,
            allow_infinity=False,
            places=2,
        )
    )
    stop = entry * (Decimal(1) - stop_pct / Decimal(100))
    # Target between 1.5x and 5x the risk above entry.
    r_mult = draw(
        st.decimals(
            min_value=Decimal("1.5"),
            max_value=Decimal("5"),
            allow_nan=False,
            allow_infinity=False,
            places=2,
        )
    )
    target = entry + (entry - stop) * r_mult
    qty = draw(
        st.decimals(
            min_value=Decimal("1"),
            max_value=Decimal("100"),
            allow_nan=False,
            allow_infinity=False,
            places=0,
        )
    )
    return OrderRequest(
        symbol="X",
        side=OrderSide.BUY,
        qty=qty,
        order_type=OrderType.LIMIT,
        entry_price=entry,
        stop_loss_price=stop,
        take_profit_price=target,
        estimated_commission_usd=Decimal("0.5"),
        bid=entry - Decimal("0.005"),
        ask=entry + Decimal("0.005"),
    )


@given(_long_request_strategy())
@settings(suppress_health_check=[HealthCheck.too_slow], deadline=None, max_examples=80)
def test_kill_switch_always_refuses(request: OrderRequest) -> None:
    """No matter what the order looks like, kill_switch=True forces refusal."""
    rm = RiskManager(_limits())
    decision = rm.approve(request, _context(kill_switch_tripped=True))
    assert not decision.approved
    assert RiskRefusalReason.KILL_SWITCH in decision.refusals


@given(_long_request_strategy())
@settings(suppress_health_check=[HealthCheck.too_slow], deadline=None, max_examples=80)
def test_open_position_always_refuses(request: OrderRequest) -> None:
    rm = RiskManager(_limits())
    decision = rm.approve(request, _context(has_open_position=True))
    assert not decision.approved
    assert RiskRefusalReason.HAS_OPEN_POSITION in decision.refusals


@given(_long_request_strategy())
@settings(suppress_health_check=[HealthCheck.too_slow], deadline=None, max_examples=80)
def test_daily_loss_cap_always_refuses(request: OrderRequest) -> None:
    rm = RiskManager(_limits(max_daily_loss_usd=Decimal("100")))
    decision = rm.approve(request, _context(daily_loss_usd=Decimal("100")))
    assert not decision.approved
    assert RiskRefusalReason.DAILY_LOSS_CAP in decision.refusals


@given(_long_request_strategy())
@settings(suppress_health_check=[HealthCheck.too_slow], deadline=None, max_examples=80)
def test_tiny_position_cap_refuses_every_order(request: OrderRequest) -> None:
    """Setting the position cap below any order's notional always refuses."""
    rm = RiskManager(_limits(max_position_size_usd=Decimal("1")))
    decision = rm.approve(request, _context())
    assert RiskRefusalReason.POSITION_SIZE_CAP in decision.refusals


@given(_long_request_strategy())
@settings(suppress_health_check=[HealthCheck.too_slow], deadline=None, max_examples=80)
def test_strict_min_r_multiple_refuses_anything_below(request: OrderRequest) -> None:
    """A min R-multiple set above the generator's max forces refusal."""
    rm = RiskManager(_limits(min_r_multiple=Decimal("10")))
    decision = rm.approve(request, _context())
    assert RiskRefusalReason.R_MULTIPLE_TOO_LOW in decision.refusals


@given(_long_request_strategy())
@settings(suppress_health_check=[HealthCheck.too_slow], deadline=None, max_examples=80)
def test_clean_context_with_tight_caps_only_refuses_with_a_reason(
    request: OrderRequest,
) -> None:
    """If the manager refuses a generated long, every refusal must come
    from a checkable risk reason — never an empty refusal list with
    `approved=False`.
    """
    rm = RiskManager(_limits())
    decision = rm.approve(request, _context())
    # Either approved or has at least one explicit refusal.
    assert decision.approved == (len(decision.refusals) == 0)


@pytest.mark.parametrize(
    "field",
    [
        "stop_loss_price",
        "take_profit_price",
        "entry_price",
    ],
)
def test_zero_price_is_invalid(field: str) -> None:
    rm = RiskManager(_limits())
    request = _long_request(**{field: Decimal("0")})
    decision = rm.approve(request, _context())
    assert RiskRefusalReason.INVALID_ORDER in decision.refusals
