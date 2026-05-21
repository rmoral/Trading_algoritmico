"""Tests for `consecutive_loss_streak` — the risk circuit-breaker input."""

from __future__ import annotations

from decimal import Decimal

from tradingbot.persistence.models import Position
from tradingbot.persistence.repositories import consecutive_loss_streak


def _closed(realized_pnl: str, commissions: str = "1") -> Position:
    """A closed position with the given gross P&L and commissions."""
    return Position(
        realized_pnl=Decimal(realized_pnl),
        commissions=Decimal(commissions),
    )


def test_empty_history_has_no_streak() -> None:
    assert consecutive_loss_streak([]) == 0


def test_counts_losses_from_most_recent() -> None:
    # Ordered most-recent-first: three losses then a win.
    history = [
        _closed("-50"),
        _closed("-20"),
        _closed("-10"),
        _closed("100"),
    ]
    assert consecutive_loss_streak(history) == 3


def test_streak_stops_at_first_win() -> None:
    history = [_closed("100"), _closed("-50"), _closed("-50")]
    assert consecutive_loss_streak(history) == 0


def test_breakeven_after_commissions_breaks_streak() -> None:
    # Gross +1, commission 1 -> net 0: not a loss, ends the streak.
    history = [_closed("1", commissions="1"), _closed("-50")]
    assert consecutive_loss_streak(history) == 0


def test_small_gain_swallowed_by_commission_is_a_loss() -> None:
    # Gross +0.5, commission 1 -> net -0.5: a loss.
    history = [_closed("0.5", commissions="1"), _closed("-50")]
    assert consecutive_loss_streak(history) == 2


def test_all_losses() -> None:
    history = [_closed("-1"), _closed("-2"), _closed("-3")]
    assert consecutive_loss_streak(history) == 3
