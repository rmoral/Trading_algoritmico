"""SQLAlchemy-backed repositories.

Each repository encapsulates the queries for one domain object. They
expose the same shape the consumer protocols expect (e.g. the
monitoring layer's `PositionsReader` / `PnLReader`) so they can be
slotted in without further adapters.

These need a live database to exercise; their tests live in
`tests/integration/` and are skipped unless `pytest -m integration`
is invoked against a running Postgres.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tradingbot.persistence.enums import PositionState
from tradingbot.persistence.models import PnLDaily, Position


class PositionsRepository:
    """Read access to positions for the monitoring layer."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def get_open_position(self) -> Position | None:
        """Return the single open position, or None when flat.

        Relies on the partial unique index `ix_positions_single_open`
        to guarantee at most one matching row.
        """
        open_states = (
            PositionState.ABRIENDO,
            PositionState.ABIERTA,
            PositionState.CERRANDO,
        )
        async with self._sessions() as session:
            result = await session.execute(
                select(Position).where(Position.state.in_(open_states))
            )
            return result.scalar_one_or_none()


class PnLRepository:
    """Read access to daily P&L."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def get_pnl_for_date(self, day: date) -> PnLDaily | None:
        async with self._sessions() as session:
            result = await session.execute(
                select(PnLDaily).where(PnLDaily.date == day)
            )
            return result.scalar_one_or_none()
