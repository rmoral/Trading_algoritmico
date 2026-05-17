"""Integration tests for SQLAlchemy-backed repositories.

These exercise the actual queries against Postgres. Skipped by
default; run with `pytest -m integration` once `docker compose up -d`
and `alembic upgrade head` have completed.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tradingbot.data import CompletedBar, DetectedLevel
from tradingbot.data.sr_strength import StrengthComponents
from tradingbot.persistence.enums import BarResolution, PositionSide, PositionState, SRKind
from tradingbot.persistence.models import Bar, PnLDaily, Position, SRLevel
from tradingbot.persistence.repositories import (
    BarRepository,
    PnLRepository,
    PositionsRepository,
    SRLevelRepository,
)

pytestmark = pytest.mark.integration


def _make_position(
    *,
    state: PositionState,
    symbol: str = "AAPL",
    side: PositionSide = PositionSide.LONG,
) -> Position:
    return Position(
        opened_at=datetime.now(UTC),
        symbol=symbol,
        side=side,
        qty=Decimal("100"),
        avg_entry_price=Decimal("150.00"),
        state=state,
    )


class TestPositionsRepository:
    async def test_returns_none_when_empty(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        repo = PositionsRepository(session_factory)
        assert await repo.get_open_position() is None

    @pytest.mark.parametrize(
        "open_state",
        [PositionState.ABRIENDO, PositionState.ABIERTA, PositionState.CERRANDO],
    )
    async def test_returns_row_for_each_open_state(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        open_state: PositionState,
    ) -> None:
        async with session_factory() as session:
            session.add(_make_position(state=open_state))
            await session.commit()

        repo = PositionsRepository(session_factory)
        row = await repo.get_open_position()
        assert row is not None
        assert row.state == open_state

    async def test_ignores_closed_rows(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        async with session_factory() as session:
            session.add(_make_position(state=PositionState.CERRADA))
            await session.commit()
        repo = PositionsRepository(session_factory)
        assert await repo.get_open_position() is None

    async def test_partial_unique_index_rejects_two_open_rows(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """`ix_positions_single_open` is the database-level guard for
        CLAUDE.md §2 principle 4.
        """
        from sqlalchemy.exc import IntegrityError

        async with session_factory() as session:
            session.add(_make_position(state=PositionState.ABIERTA, symbol="AAPL"))
            await session.commit()

        with pytest.raises(IntegrityError):
            async with session_factory() as session:
                session.add(_make_position(state=PositionState.ABIERTA, symbol="MSFT"))
                await session.commit()


class TestPnLRepository:
    async def test_returns_none_for_missing_date(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        repo = PnLRepository(session_factory)
        assert await repo.get_pnl_for_date(date(2026, 5, 16)) is None

    async def test_returns_existing_record(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        the_date = date(2026, 5, 16)
        async with session_factory() as session:
            session.add(
                PnLDaily(
                    date=the_date,
                    starting_equity=Decimal("150000"),
                    ending_equity=Decimal("150437.50"),
                    gross_pnl=Decimal("450.00"),
                    commissions=Decimal("12.50"),
                    net_pnl=Decimal("437.50"),
                    n_trades=8,
                    n_wins=5,
                    n_losses=3,
                )
            )
            await session.commit()

        repo = PnLRepository(session_factory)
        row = await repo.get_pnl_for_date(the_date)
        assert row is not None
        assert row.net_pnl == Decimal("437.50")
        assert row.n_wins == 5


def _bar(ts: datetime, *, close: str = "100.00") -> CompletedBar:
    return CompletedBar(
        symbol="AAPL",
        resolution=BarResolution.M1,
        ts=ts,
        open=Decimal("99.5"),
        high=Decimal("100.5"),
        low=Decimal("99.0"),
        close=Decimal(close),
        volume=Decimal("1000"),
        wap=Decimal("99.8"),
        count=42,
    )


class TestBarRepository:
    async def test_insert_then_read_back(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        ts = datetime(2026, 5, 16, 14, 30, tzinfo=UTC)
        repo = BarRepository(session_factory)
        await repo.insert_bar(_bar(ts, close="100.5"))

        async with session_factory() as session:
            row = (
                await session.execute(
                    select(Bar).where(
                        Bar.symbol == "AAPL",
                        Bar.resolution == BarResolution.M1.value,
                    )
                )
            ).scalar_one()
            assert row.ts == ts
            assert row.close == Decimal("100.5")

    async def test_insert_is_upsert_on_pk_collision(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Streaming the same bar twice updates instead of erroring."""
        ts = datetime(2026, 5, 16, 14, 30, tzinfo=UTC)
        repo = BarRepository(session_factory)
        await repo.insert_bar(_bar(ts, close="100.0"))
        await repo.insert_bar(_bar(ts, close="101.5"))

        async with session_factory() as session:
            rows = (
                await session.execute(
                    select(Bar).where(Bar.symbol == "AAPL")
                )
            ).scalars().all()
            assert len(rows) == 1
            assert rows[0].close == Decimal("101.5")

    async def test_get_latest_bars_returns_chronological(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        base = datetime(2026, 5, 16, 14, 30, tzinfo=UTC)
        repo = BarRepository(session_factory)
        # Insert out of order on purpose.
        await repo.insert_bar(
            _bar(base.replace(minute=32), close="103.0")
        )
        await repo.insert_bar(_bar(base, close="100.0"))
        await repo.insert_bar(
            _bar(base.replace(minute=31), close="101.0")
        )

        latest = await repo.get_latest_bars("AAPL", BarResolution.M1, 5)
        assert [str(b.close) for b in latest] == ["100.0", "101.0", "103.0"]

    async def test_get_latest_bars_caps_n(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        base = datetime(2026, 5, 16, 14, 30, tzinfo=UTC)
        repo = BarRepository(session_factory)
        for i in range(10):
            await repo.insert_bar(_bar(base.replace(minute=30 + i)))

        latest = await repo.get_latest_bars("AAPL", BarResolution.M1, 3)
        assert len(latest) == 3
        # The three most recent in chronological order.
        assert latest[0].ts.minute == 37
        assert latest[-1].ts.minute == 39


def _detected(
    *,
    symbol: str = "AAPL",
    price: str = "150.00",
    kind: SRKind = SRKind.RESISTANCE,
    strength: str = "75.0",
    detected_at: datetime | None = None,
) -> DetectedLevel:
    return DetectedLevel(
        symbol=symbol,
        kind=kind,
        price=Decimal(price),
        strength=Decimal(strength),
        components=StrengthComponents(
            clean_touches=Decimal("100"),
            volume_at_price=Decimal("60"),
            ma_confluence=Decimal("75"),
            persistence=Decimal("50"),
            rejection_quality=Decimal("40"),
        ),
        detected_at=detected_at or datetime(2026, 5, 16, 14, 30, tzinfo=UTC),
    )


class TestSRLevelRepository:
    async def test_first_upsert_inserts_row(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        repo = SRLevelRepository(session_factory)
        await repo.upsert_level(_detected(price="150.00", strength="75.0"))

        async with session_factory() as session:
            rows = (
                await session.execute(
                    select(SRLevel).where(SRLevel.symbol == "AAPL")
                )
            ).scalars().all()
            assert len(rows) == 1
            row = rows[0]
            assert row.price == Decimal("150.00")
            assert row.strength == Decimal("75.0")
            assert row.kind == SRKind.RESISTANCE.value
            assert row.ts_first_detected == row.last_update_ts
            assert row.broken_at is None
            assert row.components["clean_touches"] == "100"

    async def test_second_upsert_updates_strength_and_last_update(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        repo = SRLevelRepository(session_factory)
        t1 = datetime(2026, 5, 16, 14, 30, tzinfo=UTC)
        t2 = datetime(2026, 5, 16, 14, 45, tzinfo=UTC)

        await repo.upsert_level(
            _detected(price="150.00", strength="70.0", detected_at=t1)
        )
        await repo.upsert_level(
            _detected(price="150.00", strength="85.0", detected_at=t2)
        )

        async with session_factory() as session:
            rows = (
                await session.execute(
                    select(SRLevel).where(SRLevel.symbol == "AAPL")
                )
            ).scalars().all()
            assert len(rows) == 1  # SAME level: re-upsert updates, not insert
            row = rows[0]
            assert row.strength == Decimal("85.0")
            assert row.ts_first_detected == t1  # never moves
            assert row.last_update_ts == t2  # latest detection

    async def test_different_prices_are_different_rows(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        repo = SRLevelRepository(session_factory)
        await repo.upsert_level(_detected(price="150.00"))
        await repo.upsert_level(_detected(price="155.00"))

        async with session_factory() as session:
            rows = (
                await session.execute(
                    select(SRLevel).where(SRLevel.symbol == "AAPL")
                )
            ).scalars().all()
            assert {r.price for r in rows} == {Decimal("150.00"), Decimal("155.00")}

    async def test_different_kinds_are_different_rows(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Same symbol + price but kind differs -> two rows."""
        repo = SRLevelRepository(session_factory)
        await repo.upsert_level(_detected(price="150.00", kind=SRKind.RESISTANCE))
        await repo.upsert_level(_detected(price="150.00", kind=SRKind.SUPPORT))

        async with session_factory() as session:
            rows = (
                await session.execute(
                    select(SRLevel).where(SRLevel.symbol == "AAPL")
                )
            ).scalars().all()
            assert len(rows) == 2
            assert {r.kind for r in rows} == {SRKind.RESISTANCE.value, SRKind.SUPPORT.value}

    async def test_list_active_orders_by_strength_desc(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        repo = SRLevelRepository(session_factory)
        await repo.upsert_level(_detected(price="150.00", strength="40.0"))
        await repo.upsert_level(_detected(price="155.00", strength="80.0"))
        await repo.upsert_level(_detected(price="160.00", strength="60.0"))

        active = await repo.list_active("AAPL")
        assert [r.price for r in active] == [
            Decimal("155.00"),
            Decimal("160.00"),
            Decimal("150.00"),
        ]

    async def test_list_active_skips_broken(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        repo = SRLevelRepository(session_factory)
        await repo.upsert_level(_detected(price="150.00"))

        async with session_factory() as session:
            row = (
                await session.execute(
                    select(SRLevel).where(SRLevel.symbol == "AAPL")
                )
            ).scalar_one()
            row.broken_at = datetime(2026, 5, 16, 15, 0, tzinfo=UTC)
            await session.commit()

        assert await repo.list_active("AAPL") == []
