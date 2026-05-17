"""Tests for `OrderRouter.submit_signal`.

The broker adapter and the session factory are replaced with fakes
so the test never touches IBKR or Postgres. The risk manager IS
exercised end-to-end (it's pure and fast).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from tradingbot.execution import OrderRouter, OrderSubmissionError
from tradingbot.execution.bracket import EntryLegSpec, ExitLegSpec
from tradingbot.execution.order_router import (
    BracketSubmitter,
    SubmittedBracket,
    SubmittedLeg,
)
from tradingbot.persistence.enums import OrderSide
from tradingbot.risk import (
    RiskContext,
    RiskLimits,
    RiskManager,
    RiskRefusalReason,
)
from tradingbot.strategy.types import TradingSignal

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


def _signal() -> TradingSignal:
    """A long signal that passes every default-limit check.

    Entry 100, qty 250 -> notional 25_000 (< 50_000 cap). Stop 99.5
    -> 0.5%. Target 101 -> R=2 (>= 1.5). Profit 250 USD.
    """
    return TradingSignal(
        symbol="AAPL",
        side=OrderSide.BUY,
        entry_price=Decimal("100"),
        stop_loss_price=Decimal("99.5"),
        take_profit_price=Decimal("101"),
        qty=Decimal("250"),
        expected_profit_usd=Decimal("250"),
        expected_commission_usd=Decimal("3.50"),
        r_multiple=Decimal("2"),
        sr_level_id=None,
        sr_level_strength=Decimal("80"),
        is_partial=False,
    )


class FakeSubmitter:
    """In-memory BracketSubmitter: returns canned IDs, captures calls."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[EntryLegSpec, ExitLegSpec, ExitLegSpec]] = []
        self._next_id = 1000

    async def submit_bracket(
        self,
        entry: EntryLegSpec,
        stop_loss: ExitLegSpec,
        take_profit: ExitLegSpec,
    ) -> SubmittedBracket:
        self.calls.append((entry, stop_loss, take_profit))
        if self.fail:
            raise RuntimeError("simulated broker rejection")
        ids = [self._next_id, self._next_id + 1, self._next_id + 2]
        self._next_id += 3
        return SubmittedBracket(
            entry=SubmittedLeg(internal_id=uuid4(), ib_order_id=ids[0]),
            stop_loss=SubmittedLeg(internal_id=uuid4(), ib_order_id=ids[1]),
            take_profit=SubmittedLeg(internal_id=uuid4(), ib_order_id=ids[2]),
        )


def _fake_session_factory() -> MagicMock:
    """A no-op async_sessionmaker stand-in.

    The router only uses `factory()` as an async-context manager that
    yields a session with `.add` and `.commit` on it. We use AsyncMock
    so `await db.commit()` works.
    """
    session = MagicMock()
    session.add = MagicMock()
    session.commit = AsyncMock()

    class _Ctx:
        async def __aenter__(self) -> object:
            return session

        async def __aexit__(self, *args: object) -> None:
            return None

    factory = MagicMock(side_effect=lambda: _Ctx())
    factory.session = session  # expose for assertions
    return factory


# ---------- happy path ----------


@pytest.mark.asyncio
async def test_submit_signal_approved_submits_and_persists() -> None:
    submitter = FakeSubmitter()
    factory = _fake_session_factory()
    router = OrderRouter(
        submitter,
        RiskManager(_limits()),
        factory,
        clock=lambda: datetime(2026, 5, 16, 14, 30, tzinfo=UTC),
    )

    result = await router.submit_signal(_signal(), _context())

    assert result.approved is True
    assert result.bracket is not None
    assert len(submitter.calls) == 1
    # Three Order rows persisted, one commit.
    assert factory.session.add.call_count == 3
    factory.session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_submit_signal_refused_does_not_submit() -> None:
    submitter = FakeSubmitter()
    factory = _fake_session_factory()
    # Kill switch tripped -> immediate refusal.
    router = OrderRouter(submitter, RiskManager(_limits()), factory)
    result = await router.submit_signal(
        _signal(), _context(kill_switch_tripped=True)
    )

    assert result.approved is False
    assert RiskRefusalReason.KILL_SWITCH in result.decision.refusals
    assert result.bracket is None
    # No broker call, no DB write.
    assert submitter.calls == []
    factory.session.add.assert_not_called()
    factory.session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_submit_signal_broker_error_propagates_no_persist() -> None:
    submitter = FakeSubmitter(fail=True)
    factory = _fake_session_factory()
    router = OrderRouter(submitter, RiskManager(_limits()), factory)
    with pytest.raises(OrderSubmissionError):
        await router.submit_signal(_signal(), _context())
    # No row persisted on broker failure.
    factory.session.add.assert_not_called()
    factory.session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_signal_id_lookup_is_called_when_provided() -> None:
    looked_up: list[TradingSignal] = []
    expected_id = uuid4()

    async def lookup(signal: TradingSignal) -> UUID | None:
        looked_up.append(signal)
        return expected_id

    submitter = FakeSubmitter()
    factory = _fake_session_factory()
    router = OrderRouter(
        submitter,
        RiskManager(_limits()),
        factory,
        signal_id_lookup=lookup,
    )
    signal = _signal()
    result = await router.submit_signal(signal, _context())

    assert result.approved is True
    assert looked_up == [signal]


@pytest.mark.asyncio
async def test_short_signal_routes_through() -> None:
    short_signal = TradingSignal(
        symbol="AAPL",
        side=OrderSide.SELL,
        entry_price=Decimal("100"),
        stop_loss_price=Decimal("100.5"),
        take_profit_price=Decimal("99"),
        qty=Decimal("250"),
        expected_profit_usd=Decimal("250"),
        expected_commission_usd=Decimal("3.50"),
        r_multiple=Decimal("2"),
        sr_level_id=None,
        sr_level_strength=Decimal("80"),
        is_partial=False,
    )
    submitter = FakeSubmitter()
    factory = _fake_session_factory()
    router = OrderRouter(submitter, RiskManager(_limits()), factory)
    result = await router.submit_signal(short_signal, _context())
    assert result.approved is True
    # Entry is SELL with offset down.
    entry_leg = submitter.calls[0][0]
    assert entry_leg.side == OrderSide.SELL
    assert entry_leg.limit_price == Decimal("99.99")


def test_router_protocols_satisfied_by_fake() -> None:
    """`FakeSubmitter` satisfies the `BracketSubmitter` Protocol."""
    fake = FakeSubmitter()
    assert isinstance(fake, BracketSubmitter)


