"""Tests for `EntryTimeoutWatcher.tick`.

The repositories and the broker canceller are in-memory fakes; the
wall clock is injected so a test pins the entry's age exactly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from tradingbot.execution.entry_timeout import EntryTimeoutWatcher, OrderCanceller
from tradingbot.persistence.enums import PositionSide, PositionState
from tradingbot.persistence.models import Position

_OPENED_AT = datetime(2026, 5, 21, 15, 0, tzinfo=UTC)
_TIMEOUT_SECONDS = 5


def _position(state: PositionState) -> Position:
    return Position(
        id=uuid4(),
        opened_at=_OPENED_AT,
        symbol="AAPL",
        side=PositionSide.LONG.value,
        qty=Decimal("250"),
        avg_entry_price=Decimal("100"),
        state=state.value,
        realized_pnl=Decimal("0"),
        commissions=Decimal("0"),
    )


class FakePositionsRepo:
    def __init__(self, position: Position | None) -> None:
        self._position = position

    async def get_open_position(self) -> Position | None:
        return self._position


class FakeOrderRepo:
    def __init__(self, working: list[Any]) -> None:
        self._working = working
        self.queried_since: list[datetime] = []

    async def list_working_for_symbol(
        self, symbol: str, *, since: datetime
    ) -> list[Any]:
        self.queried_since.append(since)
        return self._working


class FakeCanceller:
    def __init__(self) -> None:
        self.cancelled: list[int] = []

    async def cancel_order(self, ib_order_id: int) -> None:
        self.cancelled.append(ib_order_id)


def _watcher(
    *,
    position: Position | None,
    age_seconds: float,
    working: list[Any] | None = None,
) -> tuple[EntryTimeoutWatcher, FakeCanceller]:
    canceller = FakeCanceller()
    watcher = EntryTimeoutWatcher(
        positions_repo=FakePositionsRepo(position),  # type: ignore[arg-type]
        order_repo=FakeOrderRepo(working or []),  # type: ignore[arg-type]
        canceller=canceller,
        timeout_seconds=_TIMEOUT_SECONDS,
        clock=lambda: _OPENED_AT + timedelta(seconds=age_seconds),
    )
    return watcher, canceller


def test_canceller_satisfies_protocol() -> None:
    assert isinstance(FakeCanceller(), OrderCanceller)


@pytest.mark.asyncio
async def test_young_entry_is_left_alone() -> None:
    watcher, canceller = _watcher(
        position=_position(PositionState.ABRIENDO),
        age_seconds=3,
        working=[SimpleNamespace(ib_order_id=100)],
    )
    await watcher.tick()
    assert canceller.cancelled == []


@pytest.mark.asyncio
async def test_stale_entry_is_cancelled() -> None:
    watcher, canceller = _watcher(
        position=_position(PositionState.ABRIENDO),
        age_seconds=10,
        working=[
            SimpleNamespace(ib_order_id=100),
            SimpleNamespace(ib_order_id=101),
            SimpleNamespace(ib_order_id=102),
        ],
    )
    await watcher.tick()
    # Parent + both bracket children are pulled.
    assert canceller.cancelled == [100, 101, 102]


@pytest.mark.asyncio
async def test_open_position_is_not_touched() -> None:
    watcher, canceller = _watcher(
        position=_position(PositionState.ABIERTA),
        age_seconds=10,
        working=[SimpleNamespace(ib_order_id=100)],
    )
    await watcher.tick()
    assert canceller.cancelled == []


@pytest.mark.asyncio
async def test_closing_position_is_not_touched() -> None:
    watcher, canceller = _watcher(
        position=_position(PositionState.CERRANDO),
        age_seconds=10,
        working=[SimpleNamespace(ib_order_id=100)],
    )
    await watcher.tick()
    assert canceller.cancelled == []


@pytest.mark.asyncio
async def test_no_open_position_is_a_noop() -> None:
    watcher, canceller = _watcher(position=None, age_seconds=10)
    await watcher.tick()
    assert canceller.cancelled == []
