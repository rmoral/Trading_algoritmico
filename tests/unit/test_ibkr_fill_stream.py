"""Tests for `IBKRFillStream` — the `ib_insync` -> domain-event bridge.

A fake `ib_insync.Event` and fake broker objects exercise the
translation without a live IBKR connection.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from tradingbot.execution.fill_handler import BrokerFill, BrokerOrderStatus
from tradingbot.execution.ibkr_fill_stream import IBKRFillStream, _safe_decimal
from tradingbot.persistence.enums import OrderStatus


class FakeEvent:
    """Minimal stand-in for `ib_insync.Event`."""

    def __init__(self) -> None:
        self.handlers: list[Any] = []

    def __iadd__(self, handler: Any) -> FakeEvent:
        self.handlers.append(handler)
        return self

    def __isub__(self, handler: Any) -> FakeEvent:
        self.handlers.remove(handler)
        return self

    def emit(self, *args: Any) -> None:
        for handler in list(self.handlers):
            handler(*args)


class FakeIB:
    def __init__(self) -> None:
        self.commissionReportEvent = FakeEvent()
        self.orderStatusEvent = FakeEvent()


class FakeClient:
    def __init__(self) -> None:
        self.ib = FakeIB()


class CapturingHandler:
    def __init__(self) -> None:
        self.fills: list[BrokerFill] = []
        self.statuses: list[BrokerOrderStatus] = []

    async def on_fill(self, fill: BrokerFill) -> None:
        self.fills.append(fill)

    async def on_order_status(self, status: BrokerOrderStatus) -> None:
        self.statuses.append(status)


def _commission_report() -> tuple[Any, Any, Any]:
    execution = SimpleNamespace(
        orderId=42,
        execId="000e-1",
        time=datetime(2026, 5, 21, 15, 0),  # naive -> normalised to UTC
        shares=250.0,
        price=100.2,
        exchange="SMART",
    )
    fill = SimpleNamespace(execution=execution)
    report = SimpleNamespace(commission=3.5)
    return None, fill, report


@pytest.mark.asyncio
async def test_commission_report_becomes_broker_fill() -> None:
    client = FakeClient()
    handler = CapturingHandler()
    stream = IBKRFillStream(client, handler)  # type: ignore[arg-type]
    stream.start()

    client.ib.commissionReportEvent.emit(*_commission_report())
    await asyncio.sleep(0.01)  # let the scheduled task run

    assert len(handler.fills) == 1
    fill = handler.fills[0]
    assert fill.ib_order_id == 42
    assert fill.exec_id == "000e-1"
    assert fill.qty == Decimal("250.0")
    assert fill.price == Decimal("100.2")
    assert fill.commission == Decimal("3.5")
    assert fill.ts.tzinfo is not None
    assert fill.ts == datetime(2026, 5, 21, 15, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_cancelled_status_becomes_broker_order_status() -> None:
    client = FakeClient()
    handler = CapturingHandler()
    stream = IBKRFillStream(client, handler)  # type: ignore[arg-type]
    stream.start()

    trade = SimpleNamespace(
        order=SimpleNamespace(orderId=7),
        orderStatus=SimpleNamespace(status="Cancelled"),
    )
    client.ib.orderStatusEvent.emit(trade)
    await asyncio.sleep(0.01)

    assert handler.statuses == [
        BrokerOrderStatus(ib_order_id=7, status=OrderStatus.CANCELLED)
    ]


@pytest.mark.asyncio
async def test_non_terminal_status_is_ignored() -> None:
    client = FakeClient()
    handler = CapturingHandler()
    stream = IBKRFillStream(client, handler)  # type: ignore[arg-type]
    stream.start()

    trade = SimpleNamespace(
        order=SimpleNamespace(orderId=7),
        orderStatus=SimpleNamespace(status="Submitted"),
    )
    client.ib.orderStatusEvent.emit(trade)
    await asyncio.sleep(0.01)

    assert handler.statuses == []


def test_stop_detaches_handlers() -> None:
    client = FakeClient()
    stream = IBKRFillStream(client, CapturingHandler())  # type: ignore[arg-type]
    stream.start()
    assert client.ib.commissionReportEvent.handlers
    stream.stop()
    assert client.ib.commissionReportEvent.handlers == []
    assert client.ib.orderStatusEvent.handlers == []


def test_safe_decimal_maps_junk_to_zero() -> None:
    assert _safe_decimal(float("nan")) == Decimal("0")
    assert _safe_decimal(float("inf")) == Decimal("0")
    assert _safe_decimal("not-a-number") == Decimal("0")
    assert _safe_decimal(3.5) == Decimal("3.5")
