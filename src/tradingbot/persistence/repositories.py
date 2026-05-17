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
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tradingbot.data.bars import CompletedBar
from tradingbot.data.sr_detector import DetectedLevel, components_to_jsonb
from tradingbot.persistence.enums import BarResolution, PositionState
from tradingbot.persistence.models import Bar, PnLDaily, Position, SRLevel


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


class BarRepository:
    """Read/write access to the `bars` hypertable.

    Implements `tradingbot.data.BarSink`.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def insert_bar(self, bar: CompletedBar) -> None:
        """Upsert one completed bar.

        ON CONFLICT updates the columns: streaming the same bar twice
        (e.g. across a reconnect) is benign.
        """
        async with self._sessions() as session:
            stmt = pg_insert(Bar).values(
                ts=bar.ts,
                symbol=bar.symbol,
                resolution=bar.resolution.value,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                wap=bar.wap,
                count=bar.count,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["ts", "symbol", "resolution"],
                set_={
                    "open": stmt.excluded.open,
                    "high": stmt.excluded.high,
                    "low": stmt.excluded.low,
                    "close": stmt.excluded.close,
                    "volume": stmt.excluded.volume,
                    "wap": stmt.excluded.wap,
                    "count": stmt.excluded.count,
                },
            )
            await session.execute(stmt)
            await session.commit()

    async def get_latest_bars(
        self, symbol: str, resolution: BarResolution, n: int
    ) -> list[Bar]:
        """Return up to `n` most recent bars for `(symbol, resolution)`."""
        async with self._sessions() as session:
            result = await session.execute(
                select(Bar)
                .where(Bar.symbol == symbol, Bar.resolution == resolution.value)
                .order_by(Bar.ts.desc())
                .limit(n)
            )
            rows = list(result.scalars().all())
            rows.reverse()  # chronological order for downstream consumers
            return rows


class SRLevelRepository:
    """Read/write access to `sr_levels`.

    Implements `tradingbot.data.sr_detector.SRLevelSink`.

    Identity for upsert is `(symbol, kind, price)`. The same physical
    level re-detected across passes updates `strength`, `components`
    and `last_update_ts`. A new (symbol, kind, price) triple inserts
    a row with `ts_first_detected = last_update_ts = detected_at`.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def upsert_level(self, level: DetectedLevel) -> None:
        components = components_to_jsonb(level.components)
        async with self._sessions() as session:
            existing = (
                await session.execute(
                    select(SRLevel).where(
                        SRLevel.symbol == level.symbol,
                        SRLevel.kind == level.kind.value,
                        SRLevel.price == level.price,
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                session.add(
                    SRLevel(
                        ts_first_detected=level.detected_at,
                        symbol=level.symbol,
                        price=level.price,
                        kind=level.kind.value,
                        strength=level.strength,
                        components=components,
                        last_update_ts=level.detected_at,
                    )
                )
            else:
                existing.strength = level.strength
                existing.components = components
                existing.last_update_ts = level.detected_at
                # broken_at left untouched: once a level is broken the
                # mark stays until the strategy decides what to do.
            await session.commit()

    async def list_active(self, symbol: str) -> list[SRLevel]:
        """Return non-broken levels for `symbol`, strongest first."""
        async with self._sessions() as session:
            result = await session.execute(
                select(SRLevel)
                .where(SRLevel.symbol == symbol, SRLevel.broken_at.is_(None))
                .order_by(SRLevel.strength.desc())
            )
            return list(result.scalars().all())
