"""Tests for `MarketClock` — minutes to the US equity RTH close."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from tradingbot.execution.market_clock import MarketClock


def _utc(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def test_mid_session_returns_minutes_remaining() -> None:
    clock = MarketClock()
    # 19:00 UTC in May = 15:00 EDT -> 60 minutes to the 16:00 close.
    assert clock.minutes_to_close(_utc(2026, 5, 21, 19, 0)) == Decimal("60")


def test_just_before_close() -> None:
    clock = MarketClock()
    # 19:57 UTC = 15:57 EDT -> 3 minutes to close.
    assert clock.minutes_to_close(_utc(2026, 5, 21, 19, 57)) == Decimal("3")


def test_before_open_is_none() -> None:
    clock = MarketClock()
    # 13:00 UTC = 09:00 EDT, before the 09:30 open.
    assert clock.minutes_to_close(_utc(2026, 5, 21, 13, 0)) is None


def test_at_close_is_none() -> None:
    clock = MarketClock()
    # 20:00 UTC = 16:00 EDT exactly: the session is over.
    assert clock.minutes_to_close(_utc(2026, 5, 21, 20, 0)) is None


def test_after_close_is_none() -> None:
    clock = MarketClock()
    assert clock.minutes_to_close(_utc(2026, 5, 21, 22, 0)) is None


def test_dst_winter_offset() -> None:
    clock = MarketClock()
    # January = EST (UTC-5): 20:00 UTC = 15:00 EST -> 60 minutes left.
    assert clock.minutes_to_close(_utc(2026, 1, 15, 20, 0)) == Decimal("60")
