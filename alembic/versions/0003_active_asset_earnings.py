"""Add is_earnings_window flag to active_asset_selections.

The operator marks, when selecting the day's asset, whether it falls
inside an earnings-announcement blackout window. The risk manager
reads it to enforce the earnings-blackout circuit breaker.

Revision ID: 0003_active_asset_earnings
Revises: 0002_active_asset
Create Date: 2026-05-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_active_asset_earnings"
down_revision: str | Sequence[str] | None = "0002_active_asset"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "active_asset_selections",
        sa.Column(
            "is_earnings_window",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("active_asset_selections", "is_earnings_window")
