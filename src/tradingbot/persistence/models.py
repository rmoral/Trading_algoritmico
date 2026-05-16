"""ORM models for all v1 tables.

Layout follows PRD.md §5. Grouping in this file is by domain via
section comments; if it grows past ~500 lines, split into submodules.

Conventions:
- UUID primary keys use `uuid.uuid4` defaults.
- Money columns are `NUMERIC(18, 6)` to preserve six decimal places
  without the rounding hazards of float.
- Timestamps are `TIMESTAMPTZ` (UTC at rest, see CLAUDE.md §8).
- JSON payloads use `JSONB` for indexing flexibility.
- Domain enums are stored as VARCHAR plus CHECK constraint so adding
  new values is a cheap migration.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from tradingbot.persistence.base import Base
from tradingbot.persistence.enums import (
    BarResolution,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
    PositionState,
    RiskSeverity,
    SRKind,
    TimeInForce,
)

MONEY = Numeric(18, 6)
PRICE = Numeric(18, 6)
QTY = Numeric(18, 6)

# Reusable check-constraint strings: keep them in one place so a new
# enum value only needs one update (here + the enum class).
_ORDER_SIDE_VALUES = ", ".join(f"'{v}'" for v in OrderSide)
_ORDER_TYPE_VALUES = ", ".join(f"'{v}'" for v in OrderType)
_ORDER_STATUS_VALUES = ", ".join(f"'{v}'" for v in OrderStatus)
_TIF_VALUES = ", ".join(f"'{v}'" for v in TimeInForce)
_POSITION_SIDE_VALUES = ", ".join(f"'{v}'" for v in PositionSide)
_POSITION_STATE_VALUES = ", ".join(f"'{v}'" for v in PositionState)
_POSITION_OPEN_STATES = "'ABRIENDO', 'ABIERTA', 'CERRANDO'"
_SR_KIND_VALUES = ", ".join(f"'{v}'" for v in SRKind)
_BAR_RES_VALUES = ", ".join(f"'{v}'" for v in BarResolution)
_RISK_SEVERITY_VALUES = ", ".join(f"'{v}'" for v in RiskSeverity)


# =========================================================
# Trading domain
# =========================================================


class Signal(Base):
    """A trading signal emitted by a strategy. Inputs to the order router."""

    __tablename__ = "signals"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    strategy: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    sr_level_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("sr_levels.id", ondelete="SET NULL"),
        nullable=True,
    )
    score: Mapped[Decimal | None] = mapped_column(Numeric(6, 3), nullable=True)
    suggested_qty: Mapped[Decimal] = mapped_column(QTY, nullable=False)
    stop_loss: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    take_profit: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    r_multiple: Mapped[Decimal | None] = mapped_column(Numeric(6, 3), nullable=True)
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        CheckConstraint(f"side IN ({_POSITION_SIDE_VALUES})", name="ck_signals_side"),
    )


class Order(Base):
    """A broker order request. One signal can spawn multiple orders (bracket)."""

    __tablename__ = "orders"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    ib_order_id: Mapped[int | None] = mapped_column(Integer, nullable=True, unique=True)
    signal_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("signals.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    parent_order_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="SET NULL"),
        nullable=True,
    )
    ts_created: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ts_submitted: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ts_filled: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ts_cancelled: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    qty: Mapped[Decimal] = mapped_column(QTY, nullable=False)
    order_type: Mapped[str] = mapped_column(String(16), nullable=False)
    limit_price: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    stop_price: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    time_in_force: Mapped[str] = mapped_column(String(8), nullable=False, default=TimeInForce.DAY)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=OrderStatus.PENDING, index=True
    )

    __table_args__ = (
        CheckConstraint(f"side IN ({_ORDER_SIDE_VALUES})", name="ck_orders_side"),
        CheckConstraint(f"order_type IN ({_ORDER_TYPE_VALUES})", name="ck_orders_type"),
        CheckConstraint(f"time_in_force IN ({_TIF_VALUES})", name="ck_orders_tif"),
        CheckConstraint(f"status IN ({_ORDER_STATUS_VALUES})", name="ck_orders_status"),
    )


class Fill(Base):
    """A single execution against an order."""

    __tablename__ = "fills"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    order_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    qty: Mapped[Decimal] = mapped_column(QTY, nullable=False)
    price: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    commission: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    exchange: Mapped[str | None] = mapped_column(String(16), nullable=True)
    exec_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)


class Position(Base):
    """A position. At most one row may have an open state at any time."""

    __tablename__ = "positions"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    qty: Mapped[Decimal] = mapped_column(QTY, nullable=False)
    avg_entry_price: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    avg_exit_price: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    realized_pnl: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    commissions: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    state: Mapped[str] = mapped_column(String(16), nullable=False, index=True)

    __table_args__ = (
        CheckConstraint(f"side IN ({_POSITION_SIDE_VALUES})", name="ck_positions_side"),
        CheckConstraint(f"state IN ({_POSITION_STATE_VALUES})", name="ck_positions_state"),
        # Enforce the single-position invariant (CLAUDE.md §2 principle 4):
        # at most one row can be in an "open" state at a time.
        Index(
            "ix_positions_single_open",
            text("(1)"),
            unique=True,
            postgresql_where=text(f"state IN ({_POSITION_OPEN_STATES})"),
        ),
    )


# =========================================================
# Market data + S/R
# =========================================================


class SRLevel(Base):
    """A detected support or resistance level with its strength score."""

    __tablename__ = "sr_levels"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    ts_first_detected: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    price: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    strength: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False)
    components: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    last_update_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    broken_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(f"kind IN ({_SR_KIND_VALUES})", name="ck_sr_levels_kind"),
    )


class Bar(Base):
    """Completed OHLCV bar at a given resolution. Converted to a hypertable."""

    __tablename__ = "bars"

    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    resolution: Mapped[str] = mapped_column(String(8), primary_key=True)
    open: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    high: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    low: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    close: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    volume: Mapped[Decimal] = mapped_column(QTY, nullable=False)
    wap: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        CheckConstraint(f"resolution IN ({_BAR_RES_VALUES})", name="ck_bars_resolution"),
    )


class Tick(Base):
    """Tick-level NBBO + last. Optional, off by default. Hypertable."""

    __tablename__ = "ticks"

    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    bid: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    ask: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    last: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    bid_size: Mapped[Decimal | None] = mapped_column(QTY, nullable=True)
    ask_size: Mapped[Decimal | None] = mapped_column(QTY, nullable=True)
    last_size: Mapped[Decimal | None] = mapped_column(QTY, nullable=True)
    exchange: Mapped[str | None] = mapped_column(String(16), nullable=True)


# =========================================================
# Operations (P&L, audit, reconciliation, config)
# =========================================================


class PnLDaily(Base):
    """End-of-day P&L summary, one row per trading date."""

    __tablename__ = "pnl_daily"

    date: Mapped[date] = mapped_column(Date, primary_key=True)
    starting_equity: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    ending_equity: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    gross_pnl: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    commissions: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    net_pnl: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    n_trades: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_wins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_losses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_dd_intraday: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)


class RiskEvent(Base):
    """Risk-manager events: trips, warnings, refusals."""

    __tablename__ = "risk_events"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str] = mapped_column(String(1024), nullable=False)
    context: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    action_taken: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        CheckConstraint(
            f"severity IN ({_RISK_SEVERITY_VALUES})",
            name="ck_risk_events_severity",
        ),
    )


class ReconciliationLog(Base):
    """Result of a startup or scheduled reconciliation pass against IBKR."""

    __tablename__ = "reconciliation_log"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    expected: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    actual: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    discrepancies: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ConfigPolicy(Base):
    """Versioned runtime configuration.

    The current row is the one with `effective_to IS NULL`. Editing
    config from the web app inserts a new row with the prior row's
    `effective_to` set to now.
    """

    __tablename__ = "config_policies"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("version", name="uq_config_policies_version"),
        # At most one "current" row.
        Index(
            "ix_config_policies_single_current",
            text("(1)"),
            unique=True,
            postgresql_where=text("effective_to IS NULL"),
        ),
    )


class AuditLog(Base):
    """Audit trail of state-changing actions (config edits, kills, resets)."""

    __tablename__ = "audit_log"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


# =========================================================
# Auth (used by the web API in Phase 2)
# =========================================================


class User(Base):
    """Operator account. Single row expected; schema supports more."""

    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    totp_secret_encrypted: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class AppSession(Base):
    """Web-app session record. Cookie carries the id."""

    __tablename__ = "app_sessions"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
