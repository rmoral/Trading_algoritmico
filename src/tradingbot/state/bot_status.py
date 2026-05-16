"""Bot status broadcast over Redis.

Three roles in this module:

- `BotStatePublisher`: writes the bot's view of its own state to Redis.
- `BotStateReader`: reads that state. Used by the web API.
- `BotStateBroadcaster`: background task that periodically publishes.
- `KillSwitchListener`: background task that polls Redis for remote
  kill requests (issued by the web API) and trips the local kill
  switch accordingly. Consumed requests are deleted so re-poll does
  not re-trip.

Keys:

- `bot:connection` — `"1"` (connected) or `"0"` (disconnected) with a
  short TTL. Absent value = the bot has not refreshed in time, treat
  as unknown / down.
- `bot:kill_switch` — JSON `{"tripped": bool, "reason": str | None}`.
  Sticky (no TTL). Reflects the bot's local kill-switch state.
- `bot:kill_switch:request` — set by the API to request a remote trip.
  The bot polls, applies, and deletes.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from redis.asyncio import Redis

from tradingbot.connector.ib_client import IBClient
from tradingbot.logging_setup import get_logger
from tradingbot.monitoring.kill_switch import KillSwitch

CONNECTION_KEY = "bot:connection"
KILL_SWITCH_KEY = "bot:kill_switch"
KILL_REQUEST_KEY = "bot:kill_switch:request"

CONNECTION_TTL_SECONDS: int = 10
DEFAULT_BROADCAST_INTERVAL_SECONDS: float = 5.0
DEFAULT_KILL_POLL_INTERVAL_SECONDS: float = 2.0


@dataclass(frozen=True)
class KillSwitchState:
    tripped: bool
    reason: str | None


def _decode(raw: bytes | str | None) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, bytes):
        return raw.decode()
    return raw


class BotStatePublisher:
    """Write the bot's state to Redis. Cheap; safe to call frequently."""

    def __init__(self, redis: Redis[bytes]) -> None:
        self._redis = redis

    async def set_connection(self, *, connected: bool) -> None:
        await self._redis.set(
            CONNECTION_KEY,
            "1" if connected else "0",
            ex=CONNECTION_TTL_SECONDS,
        )

    async def set_kill_switch(self, *, tripped: bool, reason: str | None) -> None:
        payload = json.dumps({"tripped": tripped, "reason": reason})
        await self._redis.set(KILL_SWITCH_KEY, payload)


class BotStateReader:
    """Read the bot's published state. Used by the web API."""

    def __init__(self, redis: Redis[bytes]) -> None:
        self._redis = redis

    async def get_connection(self) -> bool | None:
        """Return True (connected), False (disconnected), or None (stale/unknown)."""
        value = _decode(await self._redis.get(CONNECTION_KEY))
        if value is None:
            return None
        return value == "1"

    async def get_kill_switch(self) -> KillSwitchState | None:
        raw = _decode(await self._redis.get(KILL_SWITCH_KEY))
        if raw is None:
            return None
        data = json.loads(raw)
        return KillSwitchState(tripped=bool(data["tripped"]), reason=data.get("reason"))


class BotStateBroadcaster:
    """Periodic task: publishes the bot's local state to Redis."""

    def __init__(
        self,
        ib_client: IBClient,
        kill_switch: KillSwitch,
        publisher: BotStatePublisher,
        *,
        interval_seconds: float = DEFAULT_BROADCAST_INTERVAL_SECONDS,
    ) -> None:
        self._ib = ib_client
        self._kill = kill_switch
        self._publisher = publisher
        self._interval = interval_seconds
        self._stop = asyncio.Event()
        self._log = get_logger(__name__)

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                await self._publish_once()
            except Exception as exc:  # noqa: BLE001 - log and continue
                self._log.warning("bot_state_broadcast_failed", error=str(exc))
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
                return
            except TimeoutError:
                continue

    async def publish_now(self) -> None:
        """Force an immediate publish of the current local state.

        Called from event-driven hooks (e.g. IBClient on_connection_change)
        so the dashboard reflects transitions inside one polling cycle
        instead of waiting for the next periodic tick.
        """
        try:
            await self._publish_once()
        except Exception as exc:  # noqa: BLE001
            self._log.warning("bot_state_publish_now_failed", error=str(exc))

    async def _publish_once(self) -> None:
        await self._publisher.set_connection(connected=self._ib.is_connected())
        await self._publisher.set_kill_switch(
            tripped=self._kill.is_tripped(), reason=self._kill.reason
        )


class KillSwitchListener:
    """Polls Redis for remote kill requests and trips the local switch."""

    def __init__(
        self,
        redis: Redis[bytes],
        kill_switch: KillSwitch,
        *,
        interval_seconds: float = DEFAULT_KILL_POLL_INTERVAL_SECONDS,
    ) -> None:
        self._redis = redis
        self._kill = kill_switch
        self._interval = interval_seconds
        self._stop = asyncio.Event()
        self._log = get_logger(__name__)

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                await self._poll_once()
            except Exception as exc:  # noqa: BLE001 - log and continue
                self._log.warning("kill_switch_poll_failed", error=str(exc))
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
                return
            except TimeoutError:
                continue

    async def _poll_once(self) -> None:
        raw = _decode(await self._redis.get(KILL_REQUEST_KEY))
        if raw is None:
            return
        # Always consume the request to avoid re-trip on the next poll.
        await self._redis.delete(KILL_REQUEST_KEY)
        if not self._kill.is_tripped():
            self._log.warning("remote_kill_request_received", reason=raw)
            self._kill.trip(raw)
