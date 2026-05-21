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

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tradingbot.data.bars import CompletedBar
from tradingbot.data.sr_detector import DetectedLevel, components_to_jsonb
from tradingbot.persistence.enums import (
    BarResolution,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
    PositionState,
    TimeInForce,
)
from tradingbot.persistence.models import (
    ActiveAssetSelection,
    Bar,
    Fill,
    Order,
    PnLDaily,
    Position,
    Signal,
    SRLevel,
)
from tradingbot.strategy.state_machine import assert_transition
from tradingbot.strategy.types import TradingSignal

# Daily P&L rows carry a `starting_equity` column. Real session-open
# equity tracking is deferred (see RiskContextBuilder TODOs); until it
# lands we stamp new rows with the configured account target so the
# column is never NULL. The risk manager reads `net_pnl` / `n_trades`,
# never `starting_equity`, so this placeholder has no trading effect.
_PLACEHOLDER_STARTING_EQUITY: Decimal = Decimal("150000")


class PositionNotFoundError(RuntimeError):
    """Raised when a lifecycle write targets a position id that is absent."""


# Most recent closed positions scanned for a loss streak. The risk
# manager trips at `consecutive_losses_limit` (default 5); a 100-row
# window dwarfs any streak that could survive the daily loss cap.
_LOSS_STREAK_SCAN_LIMIT: int = 100


def consecutive_loss_streak(closed_positions: Sequence[Position]) -> int:
    """Count losing trades from the most recent backwards.

    `closed_positions` must be ordered most-recent-first. A position
    is a loss when net P&L (`realized_pnl - commissions`) is strictly
    negative; the first non-loss (win or breakeven) ends the streak.
    """
    streak = 0
    for position in closed_positions:
        if position.realized_pnl - position.commissions < 0:
            streak += 1
        else:
            break
    return streak


class PositionsRepository:
    """Read + lifecycle-write access to the `positions` table.

    Every state-changing method validates the move against
    `state_machine.assert_transition` before it touches Postgres, so
    the durable record can never disagree with the position state
    machine in CLAUDE.md §6.
    """

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

    async def recent_consecutive_losses(self) -> int:
        """Losing trades ending at the most recently closed position.

        Only realised round trips count: an `ABRIENDO -> CERRADA`
        release of an unfilled entry (`avg_exit_price IS NULL`) never
        opened, so it neither extends nor breaks the streak and is
        excluded from the scan.
        """
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(Position)
                    .where(
                        Position.state == PositionState.CERRADA.value,
                        Position.avg_exit_price.isnot(None),
                    )
                    .order_by(Position.closed_at.desc())
                    .limit(_LOSS_STREAK_SCAN_LIMIT)
                )
            ).scalars().all()
        return consecutive_loss_streak(list(rows))

    async def create_opening(
        self,
        *,
        symbol: str,
        side: PositionSide,
        qty: Decimal,
        entry_price: Decimal,
        opened_at: datetime,
    ) -> UUID:
        """Insert a fresh `ABRIENDO` position; return its id.

        The durable state is written BEFORE the broker submission it
        authorizes (CLAUDE.md §6). `entry_price` seeds
        `avg_entry_price` — a placeholder overwritten with the real
        fill VWAP by `mark_open`. `opened_at` is the creation
        timestamp and is never moved afterwards: lifecycle queries
        bound a position's orders by `orders.ts_created >= opened_at`,
        which is only sound while the column reflects creation time.

        Raises `IntegrityError` (via the `ix_positions_single_open`
        partial unique index) when another position is already open —
        the last-resort guard for the single-position invariant.
        """
        new_id = uuid4()
        async with self._sessions() as session:
            session.add(
                Position(
                    id=new_id,
                    opened_at=opened_at,
                    symbol=symbol,
                    side=side.value,
                    qty=qty,
                    avg_entry_price=entry_price,
                    state=PositionState.ABRIENDO.value,
                )
            )
            await session.commit()
        return new_id

    async def mark_open(
        self,
        position_id: UUID,
        *,
        avg_entry_price: Decimal,
        qty: Decimal,
        commissions: Decimal,
    ) -> None:
        """`ABRIENDO -> ABIERTA`: the entry order has fully filled."""
        await self._transition(
            position_id,
            PositionState.ABIERTA,
            avg_entry_price=avg_entry_price,
            qty=qty,
            commissions=commissions,
        )

    async def mark_closing(self, position_id: UUID) -> None:
        """`ABIERTA -> CERRANDO`: an exit order has started filling."""
        await self._transition(position_id, PositionState.CERRANDO)

    async def mark_closed(
        self,
        position_id: UUID,
        *,
        avg_exit_price: Decimal,
        gross_pnl: Decimal,
        commissions: Decimal,
        closed_at: datetime,
    ) -> None:
        """`CERRANDO -> CERRADA`: the exit order has fully filled.

        `gross_pnl` is the price-move P&L before costs; `commissions`
        is the round-trip total. Net P&L = `gross_pnl - commissions`.
        """
        await self._transition(
            position_id,
            PositionState.CERRADA,
            avg_exit_price=avg_exit_price,
            realized_pnl=gross_pnl,
            commissions=commissions,
            closed_at=closed_at,
        )

    async def mark_cancelled(
        self, position_id: UUID, *, closed_at: datetime
    ) -> None:
        """`ABRIENDO -> CERRADA`: the entry was rejected/cancelled unfilled.

        Releases the single-position slot so discovery can resume.
        """
        await self._transition(
            position_id, PositionState.CERRADA, closed_at=closed_at
        )

    async def _transition(
        self,
        position_id: UUID,
        to_state: PositionState,
        **fields: object,
    ) -> None:
        async with self._sessions() as session:
            pos = await session.get(Position, position_id)
            if pos is None:
                raise PositionNotFoundError(str(position_id))
            assert_transition(PositionState(pos.state), to_state)
            for name, value in fields.items():
                setattr(pos, name, value)
            pos.state = to_state.value
            await session.commit()


class PnLRepository:
    """Read + write access to daily P&L."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def get_pnl_for_date(self, day: date) -> PnLDaily | None:
        async with self._sessions() as session:
            result = await session.execute(
                select(PnLDaily).where(PnLDaily.date == day)
            )
            return result.scalar_one_or_none()

    async def record_closed_position(
        self,
        *,
        day: date,
        gross_pnl: Decimal,
        commissions: Decimal,
    ) -> None:
        """Fold one closed position into the `pnl_daily` row for `day`.

        Creates the row on the first trade of the day. `net_pnl` is
        `gross_pnl - commissions`; a strictly-positive net counts as a
        win, strictly-negative as a loss, exactly-zero as neither.
        """
        net = gross_pnl - commissions
        async with self._sessions() as session:
            row = await session.get(PnLDaily, day)
            if row is None:
                row = PnLDaily(
                    date=day,
                    starting_equity=_PLACEHOLDER_STARTING_EQUITY,
                    gross_pnl=Decimal("0"),
                    commissions=Decimal("0"),
                    net_pnl=Decimal("0"),
                    n_trades=0,
                    n_wins=0,
                    n_losses=0,
                )
                session.add(row)
            row.gross_pnl += gross_pnl
            row.commissions += commissions
            row.net_pnl += net
            row.n_trades += 1
            if net > 0:
                row.n_wins += 1
            elif net < 0:
                row.n_losses += 1
            await session.commit()


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


class ActiveAssetRepository:
    """Read access to the currently selected trading asset.

    The web app writes to `active_asset_selections` through
    `tradingbot_api.asset_service.set_active_asset`. The strategy
    engine reads here.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def get_active_symbol(self) -> str | None:
        async with self._sessions() as session:
            current = (
                await session.execute(
                    select(ActiveAssetSelection).where(
                        ActiveAssetSelection.effective_to.is_(None)
                    )
                )
            ).scalar_one_or_none()
            return current.symbol if current is not None else None


def _side_to_position_side(side: OrderSide) -> str:
    """`OrderSide.BUY` -> "LONG"; `OrderSide.SELL` -> "SHORT".

    The `signals.side` column uses LONG/SHORT (PositionSide values).
    """
    return PositionSide.LONG.value if side == OrderSide.BUY else PositionSide.SHORT.value


class SignalRepository:
    """Insert `signals` rows from in-memory `TradingSignal` instances."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def insert(
        self,
        signal: TradingSignal,
        *,
        ts: datetime,
        strategy: str = "discovery_sr",
        extra: dict[str, Any] | None = None,
    ) -> UUID:
        """Persist the signal; return the new row's id.

        The engine calls this before handing the signal to the
        `OrderRouter`, then passes the returned id so the
        `orders` row joins back to `signals`.
        """
        payload: dict[str, Any] = {
            "is_partial": signal.is_partial,
            "expected_profit_usd": str(signal.expected_profit_usd),
            "expected_commission_usd": str(signal.expected_commission_usd),
            "sr_level_strength": str(signal.sr_level_strength),
        }
        if extra:
            payload.update(extra)
        new_id = uuid4()
        async with self._sessions() as session:
            session.add(
                Signal(
                    id=new_id,
                    ts=ts,
                    strategy=strategy,
                    symbol=signal.symbol,
                    side=_side_to_position_side(signal.side),
                    sr_level_id=signal.sr_level_id,
                    score=signal.sr_level_strength,
                    suggested_qty=signal.qty,
                    stop_loss=signal.stop_loss_price,
                    take_profit=signal.take_profit_price,
                    r_multiple=signal.r_multiple,
                    extra=payload,
                )
            )
            await session.commit()
        return new_id


# Order statuses that mean the order can still execute or be cancelled.
_WORKING_ORDER_STATES: tuple[str, ...] = (
    OrderStatus.PENDING.value,
    OrderStatus.SUBMITTED.value,
    OrderStatus.PARTIAL_FILLED.value,
)


class OrderRepository:
    """Read + status-write access to the `orders` table."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def get_by_ib_order_id(self, ib_order_id: int) -> Order | None:
        """Resolve a broker-assigned id back to our `orders` row."""
        async with self._sessions() as session:
            result = await session.execute(
                select(Order).where(Order.ib_order_id == ib_order_id)
            )
            return result.scalar_one_or_none()

    async def set_status(
        self,
        order_id: UUID,
        status: OrderStatus,
        *,
        ts_filled: datetime | None = None,
        ts_cancelled: datetime | None = None,
    ) -> None:
        """Update one order's status and, when supplied, its lifecycle stamps."""
        async with self._sessions() as session:
            order = await session.get(Order, order_id)
            if order is None:
                return
            order.status = status.value
            if ts_filled is not None:
                order.ts_filled = ts_filled
            if ts_cancelled is not None:
                order.ts_cancelled = ts_cancelled
            await session.commit()

    async def list_working_for_symbol(
        self, symbol: str, *, since: datetime
    ) -> list[Order]:
        """Return still-working orders for `symbol` created at/after `since`.

        Bounded by `since = position.opened_at`: under the
        single-position invariant every order created since the open
        position appeared belongs to that position, so this is the
        exact set the end-of-day flatten must cancel.
        """
        async with self._sessions() as session:
            result = await session.execute(
                select(Order).where(
                    Order.symbol == symbol,
                    Order.ts_created >= since,
                    Order.status.in_(_WORKING_ORDER_STATES),
                )
            )
            return list(result.scalars().all())

    async def insert_exit_market_order(
        self,
        *,
        order_id: UUID,
        ib_order_id: int,
        symbol: str,
        side: OrderSide,
        qty: Decimal,
        ts: datetime,
    ) -> None:
        """Persist the end-of-day forced-flatten `MarketOrder` row.

        Recorded so the fill handler can map the flatten execution
        back to a known `orders` row.
        """
        async with self._sessions() as session:
            session.add(
                Order(
                    id=order_id,
                    ib_order_id=ib_order_id,
                    signal_id=None,
                    parent_order_id=None,
                    ts_created=ts,
                    ts_submitted=ts,
                    symbol=symbol,
                    side=side.value,
                    qty=qty,
                    order_type=OrderType.MARKET.value,
                    limit_price=None,
                    stop_price=None,
                    time_in_force=TimeInForce.DAY.value,
                    status=OrderStatus.SUBMITTED.value,
                )
            )
            await session.commit()


class FillRepository:
    """Append-only `fills` storage plus per-order execution summaries."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def record_fill(
        self,
        *,
        order_id: UUID,
        ts: datetime,
        qty: Decimal,
        price: Decimal,
        commission: Decimal,
        exchange: str | None,
        exec_id: str,
    ) -> bool:
        """Insert one execution; return True iff it was new.

        Idempotent on `exec_id`: IBKR can redeliver an execution
        across a reconnect, and the fill handler may be invoked twice
        for the same `commissionReport`. A redelivery is a no-op and
        returns False so callers skip re-processing it.
        """
        async with self._sessions() as session:
            stmt = (
                pg_insert(Fill)
                .values(
                    id=uuid4(),
                    order_id=order_id,
                    ts=ts,
                    qty=qty,
                    price=price,
                    commission=commission,
                    exchange=exchange,
                    exec_id=exec_id,
                )
                .on_conflict_do_nothing(index_elements=["exec_id"])
                .returning(Fill.id)
            )
            inserted_id = (await session.execute(stmt)).scalar_one_or_none()
            await session.commit()
            return inserted_id is not None

    async def order_fill_summary(
        self, order_id: UUID
    ) -> tuple[Decimal, Decimal, Decimal]:
        """Return `(total_qty, weighted_avg_price, total_commission)`.

        `weighted_avg_price` is `Σ(qty·price) / Σqty`; it is 0 when the
        order has no fills yet.
        """
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(
                        func.coalesce(func.sum(Fill.qty), 0),
                        func.coalesce(func.sum(Fill.qty * Fill.price), 0),
                        func.coalesce(func.sum(Fill.commission), 0),
                    ).where(Fill.order_id == order_id)
                )
            ).one()
            total_qty = Decimal(row[0])
            notional = Decimal(row[1])
            commission = Decimal(row[2])
            avg = notional / total_qty if total_qty > 0 else Decimal("0")
            return total_qty, avg, commission
