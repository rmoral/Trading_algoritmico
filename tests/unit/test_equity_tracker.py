"""Tests for `EquityTracker` against an in-memory fake Redis."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tradingbot.execution.equity_tracker import EquityTracker

_DAY1 = datetime(2026, 5, 21, 15, 0, tzinfo=UTC)
_DAY2 = datetime(2026, 5, 22, 15, 0, tzinfo=UTC)


class FakeRedis:
    """Minimal key/value fake covering get + set(ex=...)."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, *, ex: int | None = None) -> None:
        self.store[key] = value


def _tracker() -> EquityTracker:
    return EquityTracker(FakeRedis())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_no_data_reports_zero_drawdown() -> None:
    tracker = _tracker()
    assert await tracker.drawdown_pct(now=_DAY1) == Decimal("0")


@pytest.mark.asyncio
async def test_single_reading_has_zero_drawdown() -> None:
    tracker = _tracker()
    await tracker.record(Decimal("100000"), now=_DAY1)
    assert await tracker.drawdown_pct(now=_DAY1) == Decimal("0")


@pytest.mark.asyncio
async def test_drawdown_measured_from_peak() -> None:
    tracker = _tracker()
    await tracker.record(Decimal("100000"), now=_DAY1)
    await tracker.record(Decimal("98500"), now=_DAY1)
    # (100000 - 98500) / 100000 * 100 = 1.5%
    assert await tracker.drawdown_pct(now=_DAY1) == Decimal("1.5")


@pytest.mark.asyncio
async def test_recovery_above_peak_resets_drawdown() -> None:
    tracker = _tracker()
    await tracker.record(Decimal("100000"), now=_DAY1)
    await tracker.record(Decimal("98000"), now=_DAY1)
    # New high: peak rises, drawdown back to 0.
    await tracker.record(Decimal("101000"), now=_DAY1)
    assert await tracker.drawdown_pct(now=_DAY1) == Decimal("0")


@pytest.mark.asyncio
async def test_peak_is_scoped_per_trading_day() -> None:
    tracker = _tracker()
    await tracker.record(Decimal("100000"), now=_DAY1)
    await tracker.record(Decimal("90000"), now=_DAY1)
    # A new day has no recorded peak yet -> no drawdown carries over.
    assert await tracker.drawdown_pct(now=_DAY2) == Decimal("0")
