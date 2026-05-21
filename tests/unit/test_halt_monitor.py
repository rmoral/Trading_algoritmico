"""Tests for `HaltMonitor.tick` and the `halted`-tick interpretation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from tradingbot.execution.halt_monitor import HaltMonitor, _is_halted

_NOW = datetime(2026, 5, 21, 15, 0, tzinfo=UTC)


class FakeTicker:
    def __init__(self, halted: float) -> None:
        self.halted = halted


class FakeIBInner:
    def __init__(self, ticker: FakeTicker) -> None:
        self._ticker = ticker
        self.req_calls: list[Any] = []
        self.cancel_calls: list[Any] = []

    def reqMktData(self, contract: Any) -> FakeTicker:  # noqa: N802
        self.req_calls.append(contract)
        return self._ticker

    def cancelMktData(self, contract: Any) -> None:  # noqa: N802
        self.cancel_calls.append(contract)


class FakeIBClient:
    def __init__(self, *, connected: bool, ticker: FakeTicker) -> None:
        self._connected = connected
        self.ib = FakeIBInner(ticker)

    def is_connected(self) -> bool:
        return self._connected


class FakeActiveAssetRepo:
    def __init__(self, symbol: str | None) -> None:
        self.symbol = symbol

    async def get_active_symbol(self) -> str | None:
        return self.symbol


class FakeHaltStore:
    def __init__(self) -> None:
        self.updates: list[bool] = []

    async def update(self, halted: bool, *, now: datetime) -> None:
        self.updates.append(halted)


def _monitor(
    *,
    connected: bool,
    symbol: str | None,
    halted: float,
) -> tuple[HaltMonitor, FakeIBClient, FakeHaltStore]:
    ib = FakeIBClient(connected=connected, ticker=FakeTicker(halted))
    store = FakeHaltStore()
    monitor = HaltMonitor(
        ib_client=ib,  # type: ignore[arg-type]
        active_asset_repo=FakeActiveAssetRepo(symbol),  # type: ignore[arg-type]
        halt_store=store,  # type: ignore[arg-type]
        clock=lambda: _NOW,
    )
    return monitor, ib, store


# ---------- _is_halted ----------


def test_is_halted_interpretation() -> None:
    assert _is_halted(0) is False
    assert _is_halted(1) is True
    assert _is_halted(2) is True
    assert _is_halted(-1) is False
    assert _is_halted(float("nan")) is False
    assert _is_halted(None) is False


# ---------- tick ----------


@pytest.mark.asyncio
async def test_disconnected_does_nothing() -> None:
    monitor, ib, store = _monitor(connected=False, symbol="AAPL", halted=1)
    await monitor.tick()
    assert ib.ib.req_calls == []
    assert store.updates == []


@pytest.mark.asyncio
async def test_no_active_asset_does_not_subscribe() -> None:
    monitor, ib, store = _monitor(connected=True, symbol=None, halted=0)
    await monitor.tick()
    assert ib.ib.req_calls == []
    assert store.updates == []


@pytest.mark.asyncio
async def test_subscribes_and_records_not_halted() -> None:
    monitor, ib, store = _monitor(connected=True, symbol="AAPL", halted=0)
    await monitor.tick()
    assert len(ib.ib.req_calls) == 1
    assert store.updates == [False]


@pytest.mark.asyncio
async def test_records_active_halt() -> None:
    monitor, _, store = _monitor(connected=True, symbol="AAPL", halted=1)
    await monitor.tick()
    assert store.updates == [True]


@pytest.mark.asyncio
async def test_same_symbol_does_not_resubscribe() -> None:
    monitor, ib, _ = _monitor(connected=True, symbol="AAPL", halted=0)
    await monitor.tick()
    await monitor.tick()
    # One subscription, reused across both ticks.
    assert len(ib.ib.req_calls) == 1


@pytest.mark.asyncio
async def test_symbol_change_resubscribes() -> None:
    monitor, ib, _ = _monitor(connected=True, symbol="AAPL", halted=0)
    await monitor.tick()
    monitor._active_asset.symbol = "TSLA"  # type: ignore[attr-defined]
    await monitor.tick()
    assert len(ib.ib.req_calls) == 2
    # The AAPL subscription was cancelled before TSLA was opened.
    assert len(ib.ib.cancel_calls) == 1
