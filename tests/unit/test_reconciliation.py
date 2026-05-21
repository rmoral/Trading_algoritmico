"""Tests for startup reconciliation.

`classify_reconciliation` is pure and gets the full case matrix;
`Reconciler.reconcile_startup` is exercised with fakes to confirm it
trips the kill switch on a mismatch and leaves it alone when clean.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from tradingbot.connector import BrokerOpenOrder, BrokerPosition
from tradingbot.monitoring import KillSwitch
from tradingbot.persistence.enums import PositionSide, PositionState
from tradingbot.persistence.models import Position
from tradingbot.reconciliation import Reconciler, classify_reconciliation


def _position(
    *,
    state: PositionState,
    symbol: str = "AAPL",
    side: PositionSide = PositionSide.LONG,
    qty: str = "250",
) -> Position:
    return Position(
        id=uuid4(),
        opened_at=datetime(2026, 5, 21, 15, 0, tzinfo=UTC),
        symbol=symbol,
        side=side.value,
        qty=Decimal(qty),
        avg_entry_price=Decimal("100"),
        state=state.value,
        realized_pnl=Decimal("0"),
        commissions=Decimal("0"),
    )


def _broker_pos(symbol: str = "AAPL", quantity: str = "250") -> BrokerPosition:
    return BrokerPosition(
        symbol=symbol, quantity=Decimal(quantity), avg_cost=Decimal("100")
    )


def _broker_order(symbol: str = "AAPL", ib_order_id: int = 1) -> BrokerOpenOrder:
    return BrokerOpenOrder(
        ib_order_id=ib_order_id,
        symbol=symbol,
        action="BUY",
        quantity=Decimal("250"),
    )


# ---------- classify_reconciliation: clean shapes ----------


def test_both_flat_is_clean() -> None:
    assert classify_reconciliation(None, [], []) == []


def test_open_position_matching_broker_is_clean() -> None:
    issues = classify_reconciliation(
        _position(state=PositionState.ABIERTA), [_broker_pos()], []
    )
    assert issues == []


def test_abriendo_with_working_entry_is_clean() -> None:
    # Entry order still pending at the broker, no position yet.
    issues = classify_reconciliation(
        _position(state=PositionState.ABRIENDO), [], [_broker_order()]
    )
    assert issues == []


def test_closing_with_matching_broker_position_is_clean() -> None:
    issues = classify_reconciliation(
        _position(state=PositionState.CERRANDO), [_broker_pos()], []
    )
    assert issues == []


# ---------- classify_reconciliation: mismatches ----------


def test_db_flat_but_broker_holds_a_position() -> None:
    issues = classify_reconciliation(None, [_broker_pos()], [])
    assert len(issues) == 1


def test_db_flat_but_broker_has_orphan_orders() -> None:
    issues = classify_reconciliation(None, [], [_broker_order()])
    assert len(issues) == 1


def test_open_position_but_broker_flat() -> None:
    issues = classify_reconciliation(
        _position(state=PositionState.ABIERTA), [], []
    )
    assert len(issues) == 1


def test_abriendo_with_no_broker_order_or_position() -> None:
    issues = classify_reconciliation(
        _position(state=PositionState.ABRIENDO), [], []
    )
    assert len(issues) == 1


def test_abriendo_but_broker_already_holds_position() -> None:
    # The entry filled while the bot was down; DB state is stale.
    issues = classify_reconciliation(
        _position(state=PositionState.ABRIENDO), [_broker_pos()], []
    )
    assert len(issues) == 1


def test_symbol_mismatch() -> None:
    issues = classify_reconciliation(
        _position(state=PositionState.ABIERTA, symbol="AAPL"),
        [_broker_pos(symbol="TSLA")],
        [],
    )
    assert len(issues) == 1


def test_side_mismatch_long_vs_short() -> None:
    # DB says LONG, broker quantity is negative (short).
    issues = classify_reconciliation(
        _position(state=PositionState.ABIERTA, side=PositionSide.LONG),
        [_broker_pos(quantity="-250")],
        [],
    )
    assert any("side" in issue for issue in issues)


def test_quantity_mismatch() -> None:
    issues = classify_reconciliation(
        _position(state=PositionState.ABIERTA, qty="250"),
        [_broker_pos(quantity="100")],
        [],
    )
    assert any("quantity" in issue for issue in issues)


def test_multiple_broker_positions() -> None:
    issues = classify_reconciliation(
        None, [_broker_pos("AAPL"), _broker_pos("TSLA")], []
    )
    assert any("single-position invariant" in issue for issue in issues)


# ---------- Reconciler ----------


class FakeIB:
    def __init__(
        self,
        positions: list[BrokerPosition],
        orders: list[BrokerOpenOrder],
    ) -> None:
        self._positions = positions
        self._orders = orders

    async def get_positions(self) -> list[BrokerPosition]:
        return self._positions

    async def get_open_orders(self) -> list[BrokerOpenOrder]:
        return self._orders


class FakePositionsRepo:
    def __init__(self, position: Position | None) -> None:
        self._position = position

    async def get_open_position(self) -> Position | None:
        return self._position


def _session_factory() -> MagicMock:
    session = MagicMock()
    session.add = MagicMock()
    session.commit = AsyncMock()

    class _Ctx:
        async def __aenter__(self) -> object:
            return session

        async def __aexit__(self, *args: object) -> None:
            return None

    return MagicMock(side_effect=lambda: _Ctx())


def _reconciler(
    *,
    ib: FakeIB,
    db_position: Position | None,
    kill_switch: KillSwitch,
) -> Reconciler:
    return Reconciler(
        ib_client=ib,  # type: ignore[arg-type]
        positions_repo=FakePositionsRepo(db_position),  # type: ignore[arg-type]
        session_factory=_session_factory(),
        kill_switch=kill_switch,
    )


@pytest.mark.asyncio
async def test_reconcile_clean_leaves_kill_switch_alone() -> None:
    kill = KillSwitch()
    reconciler = _reconciler(
        ib=FakeIB([], []), db_position=None, kill_switch=kill
    )
    result = await reconciler.reconcile_startup()
    assert result.clean is True
    assert kill.is_tripped() is False


@pytest.mark.asyncio
async def test_reconcile_mismatch_trips_kill_switch() -> None:
    kill = KillSwitch()
    # Broker holds a position the bot knows nothing about.
    reconciler = _reconciler(
        ib=FakeIB([_broker_pos()], []), db_position=None, kill_switch=kill
    )
    result = await reconciler.reconcile_startup()
    assert result.clean is False
    assert result.discrepancies
    assert kill.is_tripped() is True
