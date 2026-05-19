"""Tests for `IBKRBracketSubmitter`.

`ib_insync` is replaced with a tiny stub at module-import time so we
can pin the exact parent/child + OCA + transmit semantics the real
broker expects.
"""

from __future__ import annotations

import sys
import types
from decimal import Decimal
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from tradingbot.connector.ib_client import IBClient
from tradingbot.execution.bracket import EntryLegSpec, ExitLegSpec
from tradingbot.persistence.enums import OrderSide, OrderType, TimeInForce

# ---------- ib_insync stub ----------


_PARENT: list[Any] = []
_SL: list[Any] = []
_TP: list[Any] = []


class FakeContract:
    def __init__(self, symbol: str, exchange: str = "SMART", currency: str = "USD") -> None:
        self.symbol = symbol
        self.exchange = exchange
        self.currency = currency


class FakeOrder:
    def __init__(self) -> None:
        self.action = ""
        self.totalQuantity = 0.0
        self.lmtPrice: float | None = None
        self.stopPrice: float | None = None
        self.orderType = ""
        self.tif = ""
        self.transmit = False
        self.ocaGroup: str | None = None
        self.ocaType: int | None = None
        self.parentId: int | None = None
        self.orderId = 0


def _FakeLimitOrder(*, action: str, totalQuantity: float, lmtPrice: float) -> FakeOrder:
    o = FakeOrder()
    o.action = action
    o.totalQuantity = totalQuantity
    o.lmtPrice = lmtPrice
    o.orderType = "LMT"
    return o


def _FakeStopOrder(*, action: str, totalQuantity: float, stopPrice: float) -> FakeOrder:
    o = FakeOrder()
    o.action = action
    o.totalQuantity = totalQuantity
    o.stopPrice = stopPrice
    o.orderType = "STP"
    return o


class FakeTrade:
    def __init__(self, order: FakeOrder) -> None:
        self.order = order


class FakeIB:
    """Stub IB. Each `placeOrder` returns a Trade with a unique orderId."""

    def __init__(self) -> None:
        self._next_id = 1000
        self.placed: list[tuple[FakeContract, FakeOrder]] = []

    def placeOrder(self, contract: FakeContract, order: FakeOrder) -> FakeTrade:
        order.orderId = self._next_id
        self._next_id += 1
        self.placed.append((contract, order))
        return FakeTrade(order)


def _install_ib_insync_stub() -> None:
    mod = types.ModuleType("ib_insync")
    mod.Stock = FakeContract  # type: ignore[attr-defined]
    mod.LimitOrder = _FakeLimitOrder  # type: ignore[attr-defined]
    mod.StopOrder = _FakeStopOrder  # type: ignore[attr-defined]
    mod.Order = FakeOrder  # type: ignore[attr-defined]
    sys.modules["ib_insync"] = mod


_install_ib_insync_stub()


# Imported AFTER the stub is installed so the lazy `from ib_insync import ...`
# picks up our fakes.
from tradingbot.execution.ibkr_submitter import IBKRBracketSubmitter  # noqa: E402

# ---------- helpers ----------


def _entry() -> EntryLegSpec:
    return EntryLegSpec(
        symbol="AAPL",
        side=OrderSide.BUY,
        qty=Decimal("250"),
        order_type=OrderType.LIMIT,
        limit_price=Decimal("100.01"),
        time_in_force=TimeInForce.DAY,
    )


def _sl() -> ExitLegSpec:
    return ExitLegSpec(
        symbol="AAPL",
        side=OrderSide.SELL,
        qty=Decimal("250"),
        order_type=OrderType.STOP,
        stop_price=Decimal("99.5"),
        limit_price=None,
        time_in_force=TimeInForce.GTC,
    )


def _tp() -> ExitLegSpec:
    return ExitLegSpec(
        symbol="AAPL",
        side=OrderSide.SELL,
        qty=Decimal("250"),
        order_type=OrderType.LIMIT,
        stop_price=None,
        limit_price=Decimal("101"),
        time_in_force=TimeInForce.GTC,
    )


def _client(ib: FakeIB) -> IBClient:
    client = MagicMock(spec=IBClient)
    client.ib = ib
    return cast(IBClient, client)


# ---------- tests ----------


@pytest.mark.asyncio
async def test_submits_three_orders_in_correct_order() -> None:
    ib = FakeIB()
    submitter = IBKRBracketSubmitter(_client(ib))
    result = await submitter.submit_bracket(_entry(), _sl(), _tp())

    assert len(ib.placed) == 3
    contracts = [p[0] for p in ib.placed]
    orders = [p[1] for p in ib.placed]
    # All three on the same contract.
    assert all(c.symbol == "AAPL" for c in contracts)
    # IDs returned in the same order.
    assert result.entry.ib_order_id == orders[0].orderId
    assert result.stop_loss.ib_order_id == orders[1].orderId
    assert result.take_profit.ib_order_id == orders[2].orderId


@pytest.mark.asyncio
async def test_transmit_flag_pattern() -> None:
    """Parent + SL = transmit False; TP (last) = transmit True."""
    ib = FakeIB()
    submitter = IBKRBracketSubmitter(_client(ib))
    await submitter.submit_bracket(_entry(), _sl(), _tp())
    parent, sl, tp = (p[1] for p in ib.placed)
    assert parent.transmit is False
    assert sl.transmit is False
    assert tp.transmit is True


@pytest.mark.asyncio
async def test_children_carry_parent_id() -> None:
    ib = FakeIB()
    submitter = IBKRBracketSubmitter(_client(ib))
    await submitter.submit_bracket(_entry(), _sl(), _tp())
    parent, sl, tp = (p[1] for p in ib.placed)
    assert sl.parentId == parent.orderId
    assert tp.parentId == parent.orderId


@pytest.mark.asyncio
async def test_children_share_oca_group_type_one() -> None:
    """SL and TP share the same OCA group with `ocaType=1`
    (cancel-with-block on fill). The parent does NOT share that group.
    """
    ib = FakeIB()
    submitter = IBKRBracketSubmitter(_client(ib))
    await submitter.submit_bracket(_entry(), _sl(), _tp())
    parent, sl, tp = (p[1] for p in ib.placed)
    assert parent.ocaGroup is None
    assert sl.ocaGroup is not None
    assert sl.ocaGroup == tp.ocaGroup
    assert sl.ocaType == 1
    assert tp.ocaType == 1


@pytest.mark.asyncio
async def test_prices_and_quantities_round_trip() -> None:
    ib = FakeIB()
    submitter = IBKRBracketSubmitter(_client(ib))
    await submitter.submit_bracket(_entry(), _sl(), _tp())
    parent, sl, tp = (p[1] for p in ib.placed)
    assert parent.action == "BUY"
    assert parent.lmtPrice == 100.01
    assert parent.totalQuantity == 250
    assert sl.action == "SELL"
    assert sl.stopPrice == 99.5
    assert tp.action == "SELL"
    assert tp.lmtPrice == 101.0


@pytest.mark.asyncio
async def test_short_bracket_inverts_actions() -> None:
    ib = FakeIB()
    short_entry = EntryLegSpec(
        symbol="AAPL",
        side=OrderSide.SELL,
        qty=Decimal("250"),
        order_type=OrderType.LIMIT,
        limit_price=Decimal("99.99"),
        time_in_force=TimeInForce.DAY,
    )
    short_sl = ExitLegSpec(
        symbol="AAPL",
        side=OrderSide.BUY,
        qty=Decimal("250"),
        order_type=OrderType.STOP,
        stop_price=Decimal("100.5"),
        limit_price=None,
        time_in_force=TimeInForce.GTC,
    )
    short_tp = ExitLegSpec(
        symbol="AAPL",
        side=OrderSide.BUY,
        qty=Decimal("250"),
        order_type=OrderType.LIMIT,
        stop_price=None,
        limit_price=Decimal("99"),
        time_in_force=TimeInForce.GTC,
    )
    submitter = IBKRBracketSubmitter(_client(ib))
    await submitter.submit_bracket(short_entry, short_sl, short_tp)
    parent, sl, tp = (p[1] for p in ib.placed)
    assert parent.action == "SELL"
    assert sl.action == "BUY"
    assert tp.action == "BUY"


@pytest.mark.asyncio
async def test_missing_stop_price_raises() -> None:
    ib = FakeIB()
    bad_sl = ExitLegSpec(
        symbol="AAPL",
        side=OrderSide.SELL,
        qty=Decimal("250"),
        order_type=OrderType.STOP,
        stop_price=None,
        limit_price=None,
        time_in_force=TimeInForce.GTC,
    )
    submitter = IBKRBracketSubmitter(_client(ib))
    with pytest.raises(ValueError):
        await submitter.submit_bracket(_entry(), bad_sl, _tp())


@pytest.mark.asyncio
async def test_missing_take_profit_price_raises() -> None:
    ib = FakeIB()
    bad_tp = ExitLegSpec(
        symbol="AAPL",
        side=OrderSide.SELL,
        qty=Decimal("250"),
        order_type=OrderType.LIMIT,
        stop_price=None,
        limit_price=None,
        time_in_force=TimeInForce.GTC,
    )
    submitter = IBKRBracketSubmitter(_client(ib))
    with pytest.raises(ValueError):
        await submitter.submit_bracket(_entry(), _sl(), bad_tp)
