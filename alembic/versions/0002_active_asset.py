"""Add active_asset_selections table.

Versioned operator selection of the day's trading asset. Same pattern
as `config_policies`: append-only, current row has `effective_to IS NULL`,
enforced by a partial unique index.

Revision ID: 0002_active_asset
Revises: 0001_initial
Create Date: 2026-05-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_active_asset"
down_revision: str | Sequence[str] | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "active_asset_selections",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("set_by", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_active_asset_single_current",
        "active_asset_selections",
        [sa.text("(1)")],
        unique=True,
        postgresql_where=sa.text("effective_to IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_active_asset_single_current",
        table_name="active_asset_selections",
    )
    op.drop_table("active_asset_selections")
