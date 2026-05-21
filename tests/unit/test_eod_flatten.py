"""Tests for `EndOfDayFlattener.tick`.

A real `MarketClock` is used with an injected wall clock so the
tests pin exactly where "now" sits relative to the RTH close. The
repositories and the broker executor are in-memory fakes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

from tradingbot.execution.eod_flatten import EndOfDayFlattener, FlattenExecutor
from tradingbot.execution.market_clock import MarketClock
from tradingbot.execution.order_router import SubmittedLeg
from tradingbot.persistence.enums import OrderSide, PositionSide, PositionState
from tradingbot.persistence.models import Position
from tradingbot.strategy.state_machine import assert_transition

# 19:57 UTC = 15:57 EDT -> 3 minutes to the 16:00 close (inside a
# 5-minute force-flatten window).
_INSIDE_WINDOW = datetime(2026, 5, 21, 19, 57, tzinfo=UTC)
# 19:50 UTC = 15:50 EDT -> 10 minutes to close (outside the window).
_OUTSIDE_WINDOW = datetime(2026, 5, 21, 19, 50, tzinfo=UTC)
# Overnight: market closed.
_OUTSIDE_RTH = datetime(2026, 5, 21, 2, 0, tzinfo=UTC)

_OPENED_AT = datetime(2026, 5, 21, 18, 0, tzinfo=UTC)


def _position(
    *,
    state: PositionState,
    side: PositionSide = PositionSide.LONG,
) -> Position:
    return Position(
        id=uuid4(),
        opened_at=_OPENED_AT,
        symbol="AAPL",
        side=side.value,
        qty=Decimal("250"),
        avg_entry_price=Decimal("100"),
        avg_exit_price=None,
        realized_pnl=Decimal("0"),
        commissions=Decimal("0"),
        state=state.value,
    )


class FakePositionsRepo:
    def __init__(self, position: Position | None) -> None:
        self._position = position
        self.closing: list[UUID] = []

    async def get_open_position(self) -> Position | None:
        return self._position

    async def mark_closing(self, position_id: UUID) -> None:
        pos = self._position
        assert pos is not None and pos.id == position_id
        assert_transition(PositionState(pos.state), PositionState.CERRANDO)
        pos.state = PositionState.CERRANDO.value
        self.closing.append(position_id)


class FakeOrderRepo:
    def __init__(self, working: list[Any]) -> None:
        self._working = working
        self.inserted: list[dict[str, object]] = []

    async def list_working_for_symbol(
        self, symbol: str, *, since: datetime
    ) -> list[Any]:
        return self._working

    async def insert_exit_market_order(self, **kwargs: object) -> None:
        self.inserted.append(kwargs)


class FakeExecutor:
    def __init__(self) -> None:
        self.cancelled: list[int] = []
        self.market_orders: list[tuple[str, OrderSide, Decimal]] = []
        self._next_id = 5000

    async def cancel_order(self, ib_order_id: int) -> None:
        self.cancelled.append(ib_order_id)

    async def submit_market_order(
        self, symbol: str, side: OrderSide, qty: Decimal
    ) -> SubmittedLeg:
        self.market_orders.append((symbol, side, qty))
        leg = SubmittedLeg(internal_id=uuid4(), ib_order_id=self._next_id)
        self._next_id += 1
        return leg


def _flattener(
    *,
    now: datetime,
    position: Position | None,
    working: list[Any] | None = None,
) -> tuple[EndOfDayFlattener, FakePositionsRepo, FakeOrderRepo, FakeExecutor]:
    positions = FakePositionsRepo(position)
    orders = FakeOrderRepo(working or [])
    executor = FakeExecutor()
    flattener = EndOfDayFlattener(
        positions_repo=positions,  # type: ignore[arg-type]
        order_repo=orders,  # type: ignore[arg-type]
        executor=executor,
        market_clock=MarketClock(),
        force_flatten_minutes=5,
        clock=lambda: now,
    )
    return flattener, positions, orders, executor


def test_executor_satisfies_protocol() -> None:
    assert isinstance(FakeExecutor(), FlattenExecutor)


@pytest.mark.asyncio
async def test_outside_window_does_nothing() -> None:
    flattener, positions, _, executor = _flattener(
        now=_OUTSIDE_WINDOW, position=_position(state=PositionState.ABIERTA)
    )
    await flattener.tick()
    assert positions.closing == []
    assert executor.market_orders == []
    assert executor.cancelled == []


@pytest.mark.asyncio
async def test_outside_rth_does_nothing() -> None:
    flattener, _, _, executor = _flattener(
        now=_OUTSIDE_RTH, position=_position(state=PositionState.ABIERTA)
    )
    await flattener.tick()
    assert executor.market_orders == []


@pytest.mark.asyncio
async def test_no_open_position_does_nothing() -> None:
    flattener, _, _, executor = _flattener(now=_INSIDE_WINDOW, position=None)
    await flattener.tick()
    assert executor.market_orders == []
    assert executor.cancelled == []


@pytest.mark.asyncio
async def test_flattens_open_long_position() -> None:
    position = _position(state=PositionState.ABIERTA, side=PositionSide.LONG)
    working = [
        SimpleNamespace(ib_order_id=100),
        SimpleNamespace(ib_order_id=101),
    ]
    flattener, positions, orders, executor = _flattener(
        now=_INSIDE_WINDOW, position=position, working=working
    )
    await flattener.tick()

    # The bracket children are cancelled before the flatten order.
    assert executor.cancelled == [100, 101]
    # The position is durably moved to CERRANDO before submission.
    assert positions.closing == [position.id]
    assert PositionState(position.state) is PositionState.CERRANDO
    # A SELL MarketOrder for the full size is submitted and recorded.
    assert executor.market_orders == [("AAPL", OrderSide.SELL, Decimal("250"))]
    assert len(orders.inserted) == 1
    assert orders.inserted[0]["side"] == OrderSide.SELL


@pytest.mark.asyncio
async def test_flattens_open_short_position_buys_back() -> None:
    position = _position(state=PositionState.ABIERTA, side=PositionSide.SHORT)
    flattener, _, _, executor = _flattener(
        now=_INSIDE_WINDOW, position=position
    )
    await flattener.tick()
    assert executor.market_orders == [("AAPL", OrderSide.BUY, Decimal("250"))]


@pytest.mark.asyncio
async def test_cancels_unfilled_entry_without_market_order() -> None:
    position = _position(state=PositionState.ABRIENDO)
    working = [SimpleNamespace(ib_order_id=200)]
    flattener, positions, _, executor = _flattener(
        now=_INSIDE_WINDOW, position=position, working=working
    )
    await flattener.tick()

    assert executor.cancelled == [200]
    # No flatten order and no state change: the cancellation event
    # releases ABRIENDO -> CERRADA via the FillHandler.
    assert executor.market_orders == []
    assert positions.closing == []


@pytest.mark.asyncio
async def test_cerrando_position_is_left_alone() -> None:
    position = _position(state=PositionState.CERRANDO)
    flattener, positions, _, executor = _flattener(
        now=_INSIDE_WINDOW, position=position
    )
    await flattener.tick()
    assert executor.cancelled == []
    assert executor.market_orders == []
    assert positions.closing == []
