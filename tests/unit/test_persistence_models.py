"""Structural assertions about the ORM metadata.

These tests run without a database. They verify that the expected
tables, primary keys, and the safety-critical indexes (single open
position; single current config policy) are declared.
"""

from __future__ import annotations

import pytest

from tradingbot.persistence import models  # noqa: F401  side-effect: register models
from tradingbot.persistence.base import Base

EXPECTED_TABLES = {
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


def test_all_expected_tables_registered() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


@pytest.mark.parametrize(
    "table,expected_pk",
    [
        ("positions", {"id"}),
        ("orders", {"id"}),
        ("fills", {"id"}),
        ("signals", {"id"}),
        ("bars", {"ts", "symbol", "resolution"}),
        ("ticks", {"ts", "symbol"}),
        ("pnl_daily", {"date"}),
    ],
)
def test_primary_keys(table: str, expected_pk: set[str]) -> None:
    pk_cols = {c.name for c in Base.metadata.tables[table].primary_key.columns}
    assert pk_cols == expected_pk


def test_positions_single_open_index_exists() -> None:
    """CLAUDE.md §2 principle 4: only one open position at a time.

    Enforced by a partial unique index on positions.
    """
    indexes = Base.metadata.tables["positions"].indexes
    assert any(idx.name == "ix_positions_single_open" for idx in indexes)


def test_config_policies_single_current_index_exists() -> None:
    """Only one config row is current (effective_to IS NULL)."""
    indexes = Base.metadata.tables["config_policies"].indexes
    assert any(idx.name == "ix_config_policies_single_current" for idx in indexes)


def test_orders_ib_order_id_unique() -> None:
    col = Base.metadata.tables["orders"].columns["ib_order_id"]
    assert col.unique is True


def test_fills_exec_id_unique() -> None:
    col = Base.metadata.tables["fills"].columns["exec_id"]
    assert col.unique is True


def test_users_username_unique() -> None:
    col = Base.metadata.tables["users"].columns["username"]
    assert col.unique is True
