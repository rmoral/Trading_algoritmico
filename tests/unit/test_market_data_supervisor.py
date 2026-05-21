"""Unit tests for `MarketDataSupervisor`.

`MarketDataService` is replaced by a fake that records subscribe /
unsubscribe calls. No IBKR connection or event loop plumbing required.
"""

from __future__ import annotations

import asyncio
from typing import cast

import pytest

from tradingbot.data.market_data import MarketDataService
from tradingbot.data.market_data_supervisor import MarketDataSupervisor


class FakeMarketData:
    """Records the symbols subscribed / unsubscribed, in order."""

    def __init__(self) -> None:
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []

    async def subscribe(self, symbol: str) -> None:
        self.subscribed.append(symbol)

    async def unsubscribe(self, symbol: str) -> None:
        self.unsubscribed.append(symbol)


class FakeActiveAsset:
    """Mutable stand-in for `ActiveAssetRepository`."""

    def __init__(self, symbol: str | None = None) -> None:
        self.symbol = symbol

    async def get_active_symbol(self) -> str | None:
        return self.symbol


def _supervisor(
    active: FakeActiveAsset,
    market_data: FakeMarketData,
    *,
    connected: bool = True,
) -> MarketDataSupervisor:
    return MarketDataSupervisor(
        active,
        cast(MarketDataService, market_data),
        connected=lambda: connected,
        poll_seconds=0.01,
    )


@pytest.mark.asyncio
async def test_sync_subscribes_new_symbol() -> None:
    md = FakeMarketData()
    sup = _supervisor(FakeActiveAsset("AAPL"), md)
    await sup.sync()
    assert md.subscribed == ["AAPL"]
    assert md.unsubscribed == []


@pytest.mark.asyncio
async def test_sync_noop_when_symbol_unchanged() -> None:
    md = FakeMarketData()
    sup = _supervisor(FakeActiveAsset("AAPL"), md)
    await sup.sync()
    await sup.sync()
    assert md.subscribed == ["AAPL"]  # second sync did nothing


@pytest.mark.asyncio
async def test_sync_switches_symbol() -> None:
    md = FakeMarketData()
    active = FakeActiveAsset("AAPL")
    sup = _supervisor(active, md)
    await sup.sync()

    active.symbol = "TSLA"
    await sup.sync()

    assert md.subscribed == ["AAPL", "TSLA"]
    assert md.unsubscribed == ["AAPL"]


@pytest.mark.asyncio
async def test_sync_unsubscribes_when_asset_cleared() -> None:
    md = FakeMarketData()
    active = FakeActiveAsset("AAPL")
    sup = _supervisor(active, md)
    await sup.sync()

    active.symbol = None
    await sup.sync()

    assert md.unsubscribed == ["AAPL"]


@pytest.mark.asyncio
async def test_sync_skips_while_disconnected() -> None:
    md = FakeMarketData()
    sup = _supervisor(FakeActiveAsset("AAPL"), md, connected=False)
    await sup.sync()
    assert md.subscribed == []


@pytest.mark.asyncio
async def test_run_stops_and_unsubscribes() -> None:
    md = FakeMarketData()
    active = FakeActiveAsset("AAPL")
    sup = _supervisor(active, md)

    async def stop_soon() -> None:
        for _ in range(50):
            if md.subscribed:
                break
            await asyncio.sleep(0.005)
        sup.stop()

    await asyncio.wait_for(asyncio.gather(sup.run(), stop_soon()), timeout=2.0)

    assert md.subscribed == ["AAPL"]
    # run() drops the live subscription on the way out.
    assert md.unsubscribed == ["AAPL"]
