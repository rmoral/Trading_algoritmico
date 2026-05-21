"""`OrderRateCounter`: a Redis-backed sliding-window order counter.

The risk manager enforces `max_orders_per_minute` (a technical rate
limit), but it can only trip if `RiskContext.recent_orders_per_minute`
reflects reality. This counter is that source of truth.

Each submitted order is one timestamped member of a Redis sorted set
(score = epoch seconds). Counting drops members older than the
window and returns the cardinality. The set lives in Redis — not bot
memory — so the count survives a bot restart, and the bot and any
future sibling process share one view of the rate.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import uuid4

from redis.asyncio import Redis

DEFAULT_KEY: str = "tradingbot:order_rate"
DEFAULT_WINDOW_SECONDS: int = 60
# TTL slack so an idle key is reclaimed without truncating the window.
_TTL_SLACK_SECONDS: int = 60


class OrderRateCounter:
    """Count orders submitted within a trailing time window."""

    def __init__(
        self,
        redis: Redis[Any],
        *,
        key: str = DEFAULT_KEY,
        window_seconds: int = DEFAULT_WINDOW_SECONDS,
    ) -> None:
        self._redis = redis
        self._key = key
        self._window = window_seconds

    async def record(self, count: int, *, now: datetime) -> None:
        """Register `count` freshly submitted orders, stamped at `now`."""
        if count <= 0:
            return
        epoch = now.timestamp()
        # Members must be unique; the uuid suffix lets several orders
        # share the same instant without collapsing into one entry.
        mapping: Mapping[str | bytes, float] = {
            f"{epoch}:{uuid4().hex}": epoch for _ in range(count)
        }
        await self._redis.zadd(self._key, mapping)
        await self._redis.expire(self._key, self._window + _TTL_SLACK_SECONDS)

    async def count_last_minute(self, now: datetime) -> int:
        """Orders submitted within `window_seconds` before `now`.

        Prunes members that have aged out of the window as a side
        effect, so the sorted set cannot grow unbounded.
        """
        cutoff = now.timestamp() - self._window
        await self._redis.zremrangebyscore(self._key, 0, cutoff)
        return int(await self._redis.zcard(self._key))


__all__ = ["OrderRateCounter"]
