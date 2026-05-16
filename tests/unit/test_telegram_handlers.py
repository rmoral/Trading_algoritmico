"""Unit tests for `TelegramHandlers`.

These run without `python-telegram-bot`. The handlers are pure async
functions that produce reply text from injected dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from tradingbot.monitoring.kill_switch import KillSwitch
from tradingbot.monitoring.telegram_bot import TelegramHandlers


@dataclass
class FakeIB:
    connected: bool = False

    def is_connected(self) -> bool:
        return self.connected


@dataclass
class FakePosition:
    symbol: str
    side: str
    qty: Decimal
    avg_entry_price: Decimal
    state: str


@dataclass
class FakePnL:
    date: date
    gross_pnl: Decimal = Decimal("0")
    commissions: Decimal = Decimal("0")
    net_pnl: Decimal = Decimal("0")
    n_trades: int = 0
    n_wins: int = 0
    n_losses: int = 0


class FakePositionsReader:
    def __init__(self, pos: FakePosition | None = None) -> None:
        self._pos = pos

    async def get_open_position(self) -> FakePosition | None:
        return self._pos


class FakePnLReader:
    def __init__(self, record: FakePnL | None = None) -> None:
        self._record = record

    async def get_pnl_for_date(self, day: date) -> FakePnL | None:
        return self._record


def _handlers(
    *,
    connected: bool = False,
    tripped: bool = False,
    open_pos: FakePosition | None = None,
    pnl: FakePnL | None = None,
    today: date | None = None,
) -> tuple[TelegramHandlers, KillSwitch]:
    ks = KillSwitch(initially_tripped=tripped)
    clock = (
        (lambda: datetime.combine(today, datetime.min.time(), tzinfo=UTC))
        if today is not None
        else (lambda: datetime.now(UTC))
    )
    h = TelegramHandlers(
        ib_client=FakeIB(connected=connected),  # type: ignore[arg-type]
        kill_switch=ks,
        positions=FakePositionsReader(open_pos),
        pnl=FakePnLReader(pnl),
        clock=clock,
    )
    return h, ks


@pytest.mark.asyncio
async def test_status_disconnected_and_idle() -> None:
    h, _ = _handlers()
    out = await h.status()
    assert "DOWN" in out
    assert "idle" in out


@pytest.mark.asyncio
async def test_status_connected_and_tripped_shows_reason() -> None:
    h, ks = _handlers(connected=True)
    ks.trip("telegram")
    out = await h.status()
    assert "connected" in out
    assert "TRIPPED" in out
    assert "telegram" in out


@pytest.mark.asyncio
async def test_positions_none() -> None:
    h, _ = _handlers()
    assert await h.positions() == "No open position."


@pytest.mark.asyncio
async def test_positions_open() -> None:
    pos = FakePosition(
        symbol="AAPL",
        side="LONG",
        qty=Decimal("250"),
        avg_entry_price=Decimal("100.50"),
        state="ABIERTA",
    )
    h, _ = _handlers(open_pos=pos)
    out = await h.positions()
    assert "AAPL" in out
    assert "LONG" in out
    assert "ABIERTA" in out


@pytest.mark.asyncio
async def test_pnl_no_record() -> None:
    today = date(2026, 5, 16)
    h, _ = _handlers(today=today)
    out = await h.pnl()
    assert "No P&L" in out
    assert "2026-05-16" in out


@pytest.mark.asyncio
async def test_pnl_record_present() -> None:
    today = date(2026, 5, 16)
    record = FakePnL(
        date=today,
        gross_pnl=Decimal("450.00"),
        commissions=Decimal("12.50"),
        net_pnl=Decimal("437.50"),
        n_trades=8,
        n_wins=5,
        n_losses=3,
    )
    h, _ = _handlers(today=today, pnl=record)
    out = await h.pnl()
    assert "437.50" in out
    assert "W 5" in out
    assert "L 3" in out


@pytest.mark.asyncio
async def test_kill_prompts_without_confirm() -> None:
    h, ks = _handlers()
    out = await h.kill()
    assert "confirm" in out.lower()
    assert ks.is_tripped() is False


@pytest.mark.asyncio
async def test_kill_with_confirm_trips() -> None:
    h, ks = _handlers()
    out = await h.kill("confirm")
    assert "TRIPPED" in out
    assert ks.is_tripped() is True
    assert ks.reason == "telegram"


@pytest.mark.asyncio
async def test_kill_when_already_tripped() -> None:
    h, ks = _handlers(tripped=True)
    out = await h.kill()
    assert "already TRIPPED" in out
    assert "startup" in out
