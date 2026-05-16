"""Integration tests for SQLAlchemy-backed repositories.

These exercise the actual queries against Postgres. Skipped by
default; run with `pytest -m integration` once `docker compose up -d`
and `alembic upgrade head` have completed.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tradingbot.persistence.enums import PositionSide, PositionState
from tradingbot.persistence.models import PnLDaily, Position
from tradingbot.persistence.repositories import PnLRepository, PositionsRepository

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
