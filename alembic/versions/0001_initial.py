"""Initial schema.

Creates all v1 tables from `tradingbot.persistence.models`, enables the
TimescaleDB extension, and converts `bars` and `ticks` into hypertables.

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


def upgrade() -> None:
    bind = op.get_bind()

    # TimescaleDB must be installed before we can convert anything into
    # a hypertable.
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE")

    Base.metadata.create_all(bind=bind)

    # Convert raw time-series tables into hypertables. `migrate_data`
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
    Base.metadata.drop_all(bind=bind)
    op.execute("DROP EXTENSION IF EXISTS timescaledb")
