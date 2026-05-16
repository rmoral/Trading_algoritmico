"""Unit tests for `tradingbot.state.bot_status`.

Uses `fakeredis` for an in-memory Redis. No docker required.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import cast

import pytest
from fakeredis import FakeAsyncRedis
from redis.asyncio import Redis

from tradingbot.connector.ib_client import IBClient
from tradingbot.monitoring.kill_switch import KillSwitch
from tradingbot.state.bot_status import (
    CONNECTION_KEY,
    KILL_REQUEST_KEY,
    KILL_SWITCH_KEY,
    BotStateBroadcaster,
    BotStatePublisher,
    BotStateReader,
    KillSwitchListener,
)


def _redis() -> Redis[bytes]:
    return cast("Redis[bytes]", FakeAsyncRedis())


@dataclass
class FakeIB:
    connected: bool = False

    def is_connected(self) -> bool:
        return self.connected


@pytest.mark.asyncio
async def test_publish_and_read_connection_round_trip() -> None:
    r = _redis()
    pub = BotStatePublisher(r)
    reader = BotStateReader(r)

    await pub.set_connection(connected=True)
    assert await reader.get_connection() is True

    await pub.set_connection(connected=False)
    assert await reader.get_connection() is False


@pytest.mark.asyncio
async def test_read_connection_returns_none_when_unset() -> None:
    reader = BotStateReader(_redis())
    assert await reader.get_connection() is None


@pytest.mark.asyncio
async def test_publish_kill_switch_round_trip() -> None:
    r = _redis()
    pub = BotStatePublisher(r)
    reader = BotStateReader(r)

    await pub.set_kill_switch(tripped=False, reason=None)
    state = await reader.get_kill_switch()
    assert state is not None
    assert state.tripped is False
    assert state.reason is None

    await pub.set_kill_switch(tripped=True, reason="telegram")
    state = await reader.get_kill_switch()
    assert state is not None
    assert state.tripped is True
    assert state.reason == "telegram"


@pytest.mark.asyncio
async def test_connection_key_has_ttl() -> None:
    r = _redis()
    pub = BotStatePublisher(r)
    await pub.set_connection(connected=True)
    ttl = await r.ttl(CONNECTION_KEY)
    assert ttl > 0  # TTL was set


@pytest.mark.asyncio
async def test_kill_switch_key_has_no_ttl() -> None:
    """Kill switch is sticky; TTL would be a footgun."""
    r = _redis()
    pub = BotStatePublisher(r)
    await pub.set_kill_switch(tripped=True, reason="x")
    ttl = await r.ttl(KILL_SWITCH_KEY)
    assert ttl == -1  # no TTL


@pytest.mark.asyncio
async def test_broadcaster_publishes_local_state() -> None:
    r = _redis()
    ib = FakeIB(connected=True)
    ks = KillSwitch()
    pub = BotStatePublisher(r)
    bc = BotStateBroadcaster(
        cast(IBClient, ib), ks, pub, interval_seconds=10.0
    )
    await bc._publish_once()  # noqa: SLF001 - testing internal hook

    reader = BotStateReader(r)
    assert await reader.get_connection() is True
    state = await reader.get_kill_switch()
    assert state is not None
    assert state.tripped is False

    ks.trip("test")
    ib.connected = False
    await bc._publish_once()  # noqa: SLF001

    assert await reader.get_connection() is False
    state = await reader.get_kill_switch()
    assert state is not None
    assert state.tripped is True
    assert state.reason == "test"


@pytest.mark.asyncio
async def test_listener_consumes_remote_kill_and_trips_local() -> None:
    r = _redis()
    ks = KillSwitch()
    listener = KillSwitchListener(r, ks, interval_seconds=10.0)

    await r.set(KILL_REQUEST_KEY, "web:operator")
    await listener._poll_once()  # noqa: SLF001

    assert ks.is_tripped() is True
    assert ks.reason == "web:operator"
    # Request was consumed.
    assert await r.get(KILL_REQUEST_KEY) is None


@pytest.mark.asyncio
async def test_listener_idempotent_when_already_tripped() -> None:
    r = _redis()
    ks = KillSwitch()
    ks.trip("local")
    listener = KillSwitchListener(r, ks, interval_seconds=10.0)

    await r.set(KILL_REQUEST_KEY, "web:operator")
    await listener._poll_once()  # noqa: SLF001

    assert ks.is_tripped() is True
    # Original reason preserved (KillSwitch.trip is idempotent).
    assert ks.reason == "local"
    # Request still consumed.
    assert await r.get(KILL_REQUEST_KEY) is None


@pytest.mark.asyncio
async def test_listener_noop_without_request() -> None:
    r = _redis()
    ks = KillSwitch()
    listener = KillSwitchListener(r, ks, interval_seconds=10.0)
    await listener._poll_once()  # noqa: SLF001
    assert ks.is_tripped() is False


@pytest.mark.asyncio
async def test_broadcaster_run_stops_promptly() -> None:
    r = _redis()
    ib = FakeIB(connected=True)
    ks = KillSwitch()
    pub = BotStatePublisher(r)
    bc = BotStateBroadcaster(
        cast(IBClient, ib), ks, pub, interval_seconds=10.0
    )

    async def stop_soon() -> None:
        await asyncio.sleep(0.01)
        bc.stop()

    await asyncio.wait_for(
        asyncio.gather(bc.run(), stop_soon()), timeout=1.0
    )


@pytest.mark.asyncio
async def test_payload_uses_safe_json() -> None:
    """Verify wire format is the documented JSON (no pickle, no eval)."""
    r = _redis()
    pub = BotStatePublisher(r)
    await pub.set_kill_switch(tripped=True, reason='"; DROP TABLE x; --')
    raw = await r.get(KILL_SWITCH_KEY)
    assert raw is not None
    parsed = json.loads(raw)
    assert parsed["tripped"] is True
    assert parsed["reason"] == '"; DROP TABLE x; --'
