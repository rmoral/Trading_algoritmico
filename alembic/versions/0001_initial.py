"""Initial schema.

Creates the v1 set of tables, enables the TimescaleDB extension, and
converts `bars` and `ticks` into hypertables.

This migration filters `Base.metadata` to the v1 tables only so that
later migrations adding new tables do not collide with what this one
created.

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-16
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# Importing models registers them with the shared metadata.
from tradingbot.persistence import models  # noqa: F401
from tradingbot.persistence.base import Base

revision: str = "0001_initial"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen list of tables this migration owns. Do not add new tables
# here; add a new migration instead.
V1_TABLES: frozenset[str] = frozenset(
    {
        "signals",
        "orders",
        "fills",
        "positions",
        "sr_levels",
        "bars",
        "ticks",
        "pnl_daily",
        "risk_events",
        "reconciliation_log",
        "config_policies",
        "audit_log",
        "users",
        "app_sessions",
    }
)


def upgrade() -> None:
    bind = op.get_bind()

    # TimescaleDB must be installed before we can convert anything into
    # a hypertable.
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE")

    tables = [t for t in Base.metadata.sorted_tables if t.name in V1_TABLES]
    Base.metadata.create_all(bind=bind, tables=tables, checkfirst=False)

    # Convert the raw time-series tables into hypertables. `migrate_data`
    # is safe here because the tables are empty at this point.
    op.execute(
        "SELECT create_hypertable('bars', 'ts', "
        "if_not_exists => TRUE, migrate_data => TRUE)"
    )
    op.execute(
        "SELECT create_hypertable('ticks', 'ts', "
        "if_not_exists => TRUE, migrate_data => TRUE)"
    )


def downgrade() -> None:
    bind = op.get_bind()
    tables = [t for t in Base.metadata.sorted_tables if t.name in V1_TABLES]
    Base.metadata.drop_all(bind=bind, tables=tables)
    op.execute("DROP EXTENSION IF EXISTS timescaledb")
