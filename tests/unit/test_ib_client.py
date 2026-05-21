"""Unit tests for `IBClient`.

The real `ib_insync.IB` is replaced by a `FakeIB` that captures
calls and lets the test drive disconnect / failure scenarios. No
real network or gateway is required.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest

from tradingbot.connector.ib_client import (
    INITIAL_BACKOFF_SECONDS,
    MAX_BACKOFF_SECONDS,
    IBClient,
)
from tradingbot.settings import Settings


@dataclass
class _AccountValue:
    tag: str
    value: str


class FakeIB:
    """Minimal in-memory stand-in for `ib_insync.IB`."""

    def __init__(self) -> None:
        self._connected = False
        self.connect_calls = 0
        self.connect_failures_remaining = 0
        self.summary: list[_AccountValue] = []
        self.disconnect_calls = 0

    def isConnected(self) -> bool:
        return self._connected

    async def connectAsync(
        self,
        host: str,
        port: int,
        clientId: int,
        timeout: float = 4.0,
        readonly: bool = False,
        account: str = "",
    ) -> None:
        self.connect_calls += 1
        if self.connect_failures_remaining > 0:
            self.connect_failures_remaining -= 1
            raise ConnectionRefusedError("simulated")
        self._connected = True

    def disconnect(self) -> None:
        self.disconnect_calls += 1
        self._connected = False

    async def accountSummaryAsync(self, account: str = "") -> list[Any]:
        return list(self.summary)


def _settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        ibkr_host="127.0.0.1",
        ibkr_port=4002,
        ibkr_client_id=1,
        live_trading=False,
    )


def _client(ib: FakeIB) -> IBClient:
    return IBClient(_settings(), ib=ib, heartbeat_seconds=0.01, connect_timeout_seconds=1.0)


@pytest.mark.asyncio
async def test_connect_succeeds_on_first_try() -> None:
    ib = FakeIB()
    client = _client(ib)
    await client.connect()
    assert client.is_connected() is True
    assert ib.connect_calls == 1


@pytest.mark.asyncio
async def test_connect_retries_with_backoff_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First two attempts fail, third succeeds; verify backoff progression."""
    sleeps: list[float] = []

    async def fake_wait_for(awaitable: Any, timeout: float) -> Any:
        sleeps.append(timeout)
        awaitable.close()  # consume to avoid un-awaited coroutine warning
        raise TimeoutError

    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)
    ib = FakeIB()
    ib.connect_failures_remaining = 2
    client = _client(ib)

    await client.connect()

    assert ib.connect_calls == 3
    assert client.is_connected() is True
    # Backoff doubles each retry, starting from INITIAL_BACKOFF_SECONDS.
    assert sleeps == [INITIAL_BACKOFF_SECONDS, INITIAL_BACKOFF_SECONDS * 2]


@pytest.mark.asyncio
async def test_backoff_caps_at_maximum(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []

    async def fake_wait_for(awaitable: Any, timeout: float) -> Any:
        sleeps.append(timeout)
        awaitable.close()  # consume to avoid un-awaited coroutine warning
        raise TimeoutError

    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)
    ib = FakeIB()
    ib.connect_failures_remaining = 20  # plenty to exceed the cap
    client = _client(ib)

    await client.connect()

    assert max(sleeps) <= MAX_BACKOFF_SECONDS
    assert MAX_BACKOFF_SECONDS in sleeps  # cap is actually hit


@pytest.mark.asyncio
async def test_stop_cancels_retry_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pending `connect()` returns promptly once `stop()` is called."""
    ib = FakeIB()
    ib.connect_failures_remaining = 100
    client = _client(ib)

    async def stop_soon() -> None:
        await asyncio.sleep(0)
        client.stop()

    await asyncio.gather(client.connect(), stop_soon())
    # We expected at least one connect attempt; we should not have
    # exhausted all 100 failures because stop_event interrupted us.
    assert ib.connect_calls < 100


@pytest.mark.asyncio
async def test_run_emits_heartbeat_then_reconnects_on_disconnect() -> None:
    """run() heartbeat-loops while connected, reconnects on disconnect, stops on stop()."""
    ib = FakeIB()
    client = _client(ib)

    async def drive() -> None:
        # Wait for first connect, then simulate gateway dropping us.
        for _ in range(50):
            if ib.isConnected():
                break
            await asyncio.sleep(0.005)
        ib._connected = False  # simulated drop
        # Let run() observe disconnect and re-enter connect()
        for _ in range(50):
            if ib.connect_calls >= 2:
                break
            await asyncio.sleep(0.005)
        client.stop()

    await asyncio.wait_for(asyncio.gather(client.run(), drive()), timeout=2.0)
    assert ib.connect_calls >= 2


@pytest.mark.asyncio
async def test_get_account_summary_returns_dict_when_connected() -> None:
    ib = FakeIB()
    ib._connected = True
    ib.summary = [
        _AccountValue("NetLiquidation", "150000.00"),
        _AccountValue("BuyingPower", "600000.00"),
    ]
    client = _client(ib)
    assert await client.get_account_summary() == {
        "NetLiquidation": "150000.00",
        "BuyingPower": "600000.00",
    }


@pytest.mark.asyncio
async def test_get_account_summary_empty_when_disconnected() -> None:
    ib = FakeIB()
    ib._connected = False
    client = _client(ib)
    assert await client.get_account_summary() == {}


@pytest.mark.asyncio
async def test_connection_listener_fires_on_connect_and_disconnect() -> None:
    """`on_connection_change` is called once on connect, once on drop."""
    ib = FakeIB()
    events: list[bool] = []

    async def listener(connected: bool) -> None:
        events.append(connected)

    client = IBClient(
        _settings(),
        ib=ib,
        heartbeat_seconds=0.005,
        connect_timeout_seconds=1.0,
        on_connection_change=listener,
    )

    async def drive() -> None:
        for _ in range(50):
            if ib.isConnected():
                break
            await asyncio.sleep(0.005)
        ib._connected = False  # simulated drop
        for _ in range(50):
            if len(events) >= 2:
                break
            await asyncio.sleep(0.005)
        client.stop()

    await asyncio.wait_for(asyncio.gather(client.run(), drive()), timeout=2.0)
    assert events[0] is True
    assert events[-1] is False


@pytest.mark.asyncio
async def test_listener_failure_does_not_break_run_loop() -> None:
    """A raising listener is logged-and-swallowed; the bot keeps running."""
    ib = FakeIB()
    calls = 0

    async def listener(_connected: bool) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("listener boom")

    client = IBClient(
        _settings(),
        ib=ib,
        heartbeat_seconds=0.005,
        connect_timeout_seconds=1.0,
        on_connection_change=listener,
    )

    async def drive() -> None:
        for _ in range(50):
            if calls >= 1:
                break
            await asyncio.sleep(0.005)
        client.stop()

    await asyncio.wait_for(asyncio.gather(client.run(), drive()), timeout=2.0)
    assert calls >= 1
    # The connect succeeded despite the listener raising.
    assert client.is_connected() is True
