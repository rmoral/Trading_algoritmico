"""Tests for `FillHandler`.

The four repositories are replaced with in-memory fakes so the tests
never touch Postgres. The fakes apply the same state-machine
validation the real `PositionsRepository` does, so an illegal
transition surfaces here just as it would in production.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from tradingbot.execution.fill_handler import (
    BrokerFill,
    BrokerOrderStatus,
    FillHandler,
)
from tradingbot.persistence.enums import (
    OrderSide,
    OrderStatus,
    PositionSide,
    PositionState,
)
from tradingbot.persistence.models import Order, Position
from tradingbot.strategy.state_machine import assert_transition

_TS = datetime(2026, 5, 21, 15, 0, tzinfo=UTC)


# ---------- fakes ----------


def _order(
    *,
    ib_order_id: int,
    side: OrderSide,
    qty: Decimal = Decimal("250"),
) -> Order:
    return Order(
        id=uuid4(),
        ib_order_id=ib_order_id,
        signal_id=None,
        parent_order_id=None,
        ts_created=_TS,
        symbol="AAPL",
        side=side.value,
        qty=qty,
        order_type="LIMIT",
        status=OrderStatus.SUBMITTED.value,
    )


def _position(
    *,
    side: PositionSide = PositionSide.LONG,
    state: PositionState = PositionState.ABRIENDO,
    qty: Decimal = Decimal("250"),
    avg_entry_price: Decimal = Decimal("100"),
) -> Position:
    return Position(
        id=uuid4(),
        opened_at=_TS,
        symbol="AAPL",
        side=side.value,
        qty=qty,
        avg_entry_price=avg_entry_price,
        avg_exit_price=None,
        realized_pnl=Decimal("0"),
        commissions=Decimal("0"),
        state=state.value,
    )


class FakeOrderRepo:
    def __init__(self, orders: list[Order]) -> None:
        self._by_ib = {o.ib_order_id: o for o in orders}
        self._by_id = {o.id: o for o in orders}

    async def get_by_ib_order_id(self, ib_order_id: int) -> Order | None:
        return self._by_ib.get(ib_order_id)

    async def set_status(
        self,
        order_id: UUID,
        status: OrderStatus,
        *,
        ts_filled: datetime | None = None,
        ts_cancelled: datetime | None = None,
    ) -> None:
        order = self._by_id.get(order_id)
        if order is None:
            return
        order.status = status.value
        if ts_filled is not None:
            order.ts_filled = ts_filled
        if ts_cancelled is not None:
            order.ts_cancelled = ts_cancelled


class FakeFillRepo:
    def __init__(self) -> None:
        self._seen: set[str] = set()
        self._fills: list[dict[str, object]] = []

    async def record_fill(
        self,
        *,
        order_id: UUID,
        ts: datetime,
        qty: Decimal,
        price: Decimal,
        commission: Decimal,
        exchange: str | None,
        exec_id: str,
    ) -> bool:
        if exec_id in self._seen:
            return False
        self._seen.add(exec_id)
        self._fills.append(
            {
                "order_id": order_id,
                "qty": qty,
                "price": price,
                "commission": commission,
            }
        )
        return True

    async def order_fill_summary(
        self, order_id: UUID
    ) -> tuple[Decimal, Decimal, Decimal]:
        rows = [f for f in self._fills if f["order_id"] == order_id]
        total = sum((f["qty"] for f in rows), Decimal("0"))
        notional = sum((f["qty"] * f["price"] for f in rows), Decimal("0"))
        commission = sum((f["commission"] for f in rows), Decimal("0"))
        avg = notional / total if total > 0 else Decimal("0")
        return total, avg, commission


class FakePositionsRepo:
    def __init__(self, position: Position | None) -> None:
        self._position = position

    async def get_open_position(self) -> Position | None:
        pos = self._position
        if pos is None or PositionState(pos.state) is PositionState.CERRADA:
            return None
        return pos

    def _move(self, position_id: UUID, to_state: PositionState) -> Position:
        pos = self._position
        assert pos is not None and pos.id == position_id
        assert_transition(PositionState(pos.state), to_state)
        pos.state = to_state.value
        return pos

    async def mark_open(
        self,
        position_id: UUID,
        *,
        avg_entry_price: Decimal,
        qty: Decimal,
        commissions: Decimal,
    ) -> None:
        pos = self._move(position_id, PositionState.ABIERTA)
        pos.avg_entry_price = avg_entry_price
        pos.qty = qty
        pos.commissions = commissions

    async def mark_closing(self, position_id: UUID) -> None:
        self._move(position_id, PositionState.CERRANDO)

    async def mark_closed(
        self,
        position_id: UUID,
        *,
        avg_exit_price: Decimal,
        gross_pnl: Decimal,
        commissions: Decimal,
        closed_at: datetime,
    ) -> None:
        pos = self._move(position_id, PositionState.CERRADA)
        pos.avg_exit_price = avg_exit_price
        pos.realized_pnl = gross_pnl
        pos.commissions = commissions
        pos.closed_at = closed_at

    async def mark_cancelled(
        self, position_id: UUID, *, closed_at: datetime
    ) -> None:
        pos = self._move(position_id, PositionState.CERRADA)
        pos.closed_at = closed_at


class FakePnLRepo:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def record_closed_position(
        self, *, day: date, gross_pnl: Decimal, commissions: Decimal
    ) -> None:
        self.calls.append(
            {"day": day, "gross_pnl": gross_pnl, "commissions": commissions}
        )


def _handler(
    *,
    orders: list[Order],
    position: Position | None,
) -> tuple[FillHandler, FakePositionsRepo, FakePnLRepo]:
    positions = FakePositionsRepo(position)
    pnl = FakePnLRepo()
    handler = FillHandler(
        positions_repo=positions,  # type: ignore[arg-type]
        order_repo=FakeOrderRepo(orders),  # type: ignore[arg-type]
        fill_repo=FakeFillRepo(),  # type: ignore[arg-type]
        pnl_repo=pnl,  # type: ignore[arg-type]
        lookup_attempts=1,
    )
    return handler, positions, pnl


def _fill(ib_order_id: int, *, exec_id: str, qty: str, price: str) -> BrokerFill:
    return BrokerFill(
        ib_order_id=ib_order_id,
        exec_id=exec_id,
        ts=_TS,
        qty=Decimal(qty),
        price=Decimal(price),
        commission=Decimal("3.50"),
        exchange="SMART",
    )


# ---------- entry fills ----------


@pytest.mark.asyncio
async def test_entry_fill_opens_position() -> None:
    entry = _order(ib_order_id=1, side=OrderSide.BUY)
    position = _position(state=PositionState.ABRIENDO)
    handler, positions, _ = _handler(orders=[entry], position=position)

    await handler.on_fill(_fill(1, exec_id="e1", qty="250", price="100.20"))

    assert PositionState(position.state) is PositionState.ABIERTA
    assert position.avg_entry_price == Decimal("100.20")
    assert position.commissions == Decimal("3.50")
    assert entry.status == OrderStatus.FILLED.value


@pytest.mark.asyncio
async def test_partial_entry_fill_stays_abriendo() -> None:
    entry = _order(ib_order_id=1, side=OrderSide.BUY)
    position = _position(state=PositionState.ABRIENDO)
    handler, _, _ = _handler(orders=[entry], position=position)

    await handler.on_fill(_fill(1, exec_id="e1", qty="100", price="100.20"))

    assert PositionState(position.state) is PositionState.ABRIENDO
    assert entry.status == OrderStatus.PARTIAL_FILLED.value


# ---------- exit fills ----------


@pytest.mark.asyncio
async def test_exit_fill_closes_long_position() -> None:
    exit_order = _order(ib_order_id=2, side=OrderSide.SELL)
    position = _position(
        state=PositionState.ABIERTA,
        side=PositionSide.LONG,
        avg_entry_price=Decimal("100"),
    )
    position.commissions = Decimal("3.50")  # entry commission, set by mark_open
    handler, _, pnl = _handler(orders=[exit_order], position=position)

    await handler.on_fill(_fill(2, exec_id="x1", qty="250", price="101"))

    assert PositionState(position.state) is PositionState.CERRADA
    assert position.avg_exit_price == Decimal("101")
    # gross = (101 - 100) * 250 = 250
    assert position.realized_pnl == Decimal("250")
    # round-trip commission = entry 3.50 + exit 3.50
    assert position.commissions == Decimal("7.00")
    assert pnl.calls == [
        {
            "day": _TS.date(),
            "gross_pnl": Decimal("250"),
            "commissions": Decimal("7.00"),
        }
    ]


@pytest.mark.asyncio
async def test_exit_fill_closes_short_position() -> None:
    exit_order = _order(ib_order_id=2, side=OrderSide.BUY)
    position = _position(
        state=PositionState.ABIERTA,
        side=PositionSide.SHORT,
        avg_entry_price=Decimal("100"),
    )
    handler, _, _ = _handler(orders=[exit_order], position=position)

    await handler.on_fill(_fill(2, exec_id="x1", qty="250", price="99"))

    assert PositionState(position.state) is PositionState.CERRADA
    # short gross = (100 - 99) * 250 = 250
    assert position.realized_pnl == Decimal("250")


# ---------- idempotency / robustness ----------


@pytest.mark.asyncio
async def test_duplicate_exec_id_is_ignored() -> None:
    entry = _order(ib_order_id=1, side=OrderSide.BUY)
    position = _position(state=PositionState.ABRIENDO)
    handler, _, _ = _handler(orders=[entry], position=position)

    await handler.on_fill(_fill(1, exec_id="e1", qty="250", price="100"))
    # A redelivery of the same execution must not double-count.
    await handler.on_fill(_fill(1, exec_id="e1", qty="250", price="100"))

    assert PositionState(position.state) is PositionState.ABIERTA
    assert position.commissions == Decimal("3.50")


@pytest.mark.asyncio
async def test_unknown_order_is_skipped() -> None:
    position = _position(state=PositionState.ABRIENDO)
    handler, _, _ = _handler(orders=[], position=position)

    # No order with ib_order_id 99 exists; must not raise.
    await handler.on_fill(_fill(99, exec_id="e1", qty="250", price="100"))

    assert PositionState(position.state) is PositionState.ABRIENDO


# ---------- cancellation ----------


@pytest.mark.asyncio
async def test_entry_cancellation_releases_position() -> None:
    entry = _order(ib_order_id=1, side=OrderSide.BUY)
    position = _position(state=PositionState.ABRIENDO)
    handler, _, _ = _handler(orders=[entry], position=position)

    await handler.on_order_status(
        BrokerOrderStatus(ib_order_id=1, status=OrderStatus.CANCELLED)
    )

    assert PositionState(position.state) is PositionState.CERRADA
    assert entry.status == OrderStatus.CANCELLED.value
