"""Tests for `EquityMonitor.tick`."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tradingbot.execution.equity_monitor import EquityMonitor

_NOW = datetime(2026, 5, 21, 15, 0, tzinfo=UTC)


class FakeIB:
    def __init__(self, *, connected: bool, summary: dict[str, str]) -> None:
        self._connected = connected
        self._summary = summary

    def is_connected(self) -> bool:
        return self._connected

    async def get_account_summary(self) -> dict[str, str]:
        return self._summary


class FakeTracker:
    def __init__(self) -> None:
        self.recorded: list[Decimal] = []

    async def record(self, equity: Decimal, *, now: datetime) -> None:
        self.recorded.append(equity)


def _monitor(ib: FakeIB) -> tuple[EquityMonitor, FakeTracker]:
    tracker = FakeTracker()
    monitor = EquityMonitor(
        ib_client=ib,  # type: ignore[arg-type]
        equity_tracker=tracker,  # type: ignore[arg-type]
        clock=lambda: _NOW,
    )
    return monitor, tracker


@pytest.mark.asyncio
async def test_records_net_liquidation_when_connected() -> None:
    monitor, tracker = _monitor(
        FakeIB(connected=True, summary={"NetLiquidation": "152340.55"})
    )
    await monitor.tick()
    assert tracker.recorded == [Decimal("152340.55")]


@pytest.mark.asyncio
async def test_disconnected_records_nothing() -> None:
    monitor, tracker = _monitor(
        FakeIB(connected=False, summary={"NetLiquidation": "150000"})
    )
    await monitor.tick()
    assert tracker.recorded == []


@pytest.mark.asyncio
async def test_missing_tag_records_nothing() -> None:
    monitor, tracker = _monitor(
        FakeIB(connected=True, summary={"BuyingPower": "300000"})
    )
    await monitor.tick()
    assert tracker.recorded == []


@pytest.mark.asyncio
async def test_unparseable_value_records_nothing() -> None:
    monitor, tracker = _monitor(
        FakeIB(connected=True, summary={"NetLiquidation": "n/a"})
    )
    await monitor.tick()
    assert tracker.recorded == []
