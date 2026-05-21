"""Tests for `OrderRateCounter` against an in-memory fake Redis."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tradingbot.execution.order_rate import OrderRateCounter

_T0 = datetime(2026, 5, 21, 15, 0, tzinfo=UTC)


class FakeRedis:
    """Minimal single-key sorted-set fake covering the ops used here."""

    def __init__(self) -> None:
        self.zset: dict[str, float] = {}
        self.expire_calls: list[int] = []

    async def zadd(self, _key: str, mapping: dict[str, float]) -> None:
        self.zset.update(mapping)

    async def expire(self, _key: str, ttl: int) -> None:
        self.expire_calls.append(ttl)

    async def zremrangebyscore(
        self, _key: str, min_score: float, max_score: float
    ) -> None:
        stale = [
            member
            for member, score in self.zset.items()
            if min_score <= score <= max_score
        ]
        for member in stale:
            del self.zset[member]

    async def zcard(self, _key: str) -> int:
        return len(self.zset)


def _counter() -> tuple[OrderRateCounter, FakeRedis]:
    redis = FakeRedis()
    return OrderRateCounter(redis), redis  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_records_and_counts_within_window() -> None:
    counter, _ = _counter()
    await counter.record(3, now=_T0)
    assert await counter.count_last_minute(_T0) == 3


@pytest.mark.asyncio
async def test_zero_count_is_a_noop() -> None:
    counter, redis = _counter()
    await counter.record(0, now=_T0)
    assert redis.zset == {}
    assert await counter.count_last_minute(_T0) == 0


@pytest.mark.asyncio
async def test_old_entries_age_out_of_the_window() -> None:
    counter, _ = _counter()
    await counter.record(3, now=_T0)
    # 90s later the original batch is outside the 60s window.
    later = _T0 + timedelta(seconds=90)
    assert await counter.count_last_minute(later) == 0


@pytest.mark.asyncio
async def test_sliding_window_keeps_only_recent_orders() -> None:
    counter, _ = _counter()
    await counter.record(3, now=_T0)
    await counter.record(3, now=_T0 + timedelta(seconds=30))
    # At +30s both batches are inside the window.
    assert await counter.count_last_minute(_T0 + timedelta(seconds=30)) == 6
    # At +70s only the second batch (placed at +30s) remains.
    assert await counter.count_last_minute(_T0 + timedelta(seconds=70)) == 3


@pytest.mark.asyncio
async def test_record_sets_a_ttl() -> None:
    counter, redis = _counter()
    await counter.record(1, now=_T0)
    assert redis.expire_calls and redis.expire_calls[-1] > 60
