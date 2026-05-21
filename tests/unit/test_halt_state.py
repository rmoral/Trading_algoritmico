"""Tests for `HaltStateStore` against an in-memory fake Redis."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tradingbot.execution.halt_state import HaltStateStore

_NOW = datetime(2026, 5, 21, 15, 30, tzinfo=UTC)


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, *, ex: int | None = None) -> None:
        self.store[key] = value


def _store() -> HaltStateStore:
    return HaltStateStore(FakeRedis())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_fresh_store_reports_not_halted() -> None:
    store = _store()
    active, resumed_at = await store.state()
    assert active is False
    assert resumed_at is None


@pytest.mark.asyncio
async def test_halt_is_recorded() -> None:
    store = _store()
    await store.update(True, now=_NOW)
    active, resumed_at = await store.state()
    assert active is True
    # No resume has happened yet.
    assert resumed_at is None


@pytest.mark.asyncio
async def test_resume_stamps_the_resumed_timestamp() -> None:
    store = _store()
    await store.update(True, now=_NOW)
    resume_time = datetime(2026, 5, 21, 15, 35, tzinfo=UTC)
    await store.update(False, now=resume_time)
    active, resumed_at = await store.state()
    assert active is False
    assert resumed_at == resume_time


@pytest.mark.asyncio
async def test_not_halted_without_a_prior_halt_sets_no_timestamp() -> None:
    store = _store()
    await store.update(False, now=_NOW)
    active, resumed_at = await store.state()
    assert active is False
    assert resumed_at is None
