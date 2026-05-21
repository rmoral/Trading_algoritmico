"""Startup reconciliation: the bot's durable state vs IBKR's reality.

CLAUDE.md §2 principle 7 requires the bot to reconcile its world view
on restart. IBKR's account is the source of truth for what is
*actually* held; Postgres is what the bot *believes*. A restart
mid-position, a fill that landed while the bot was down, or a manual
intervention in TWS can make the two disagree.

`Reconciler.reconcile_startup` compares the two before any trading
task starts. The policy is deliberately conservative: it never tries
to auto-heal a divergence. If anything disagrees it writes a
`reconciliation_log` row, trips the kill switch, and reports
`clean=False` — the entry point then keeps the bot connected (so the
operator can inspect it and use `scripts/`) but starts no trading.
A clean result is the only path to live discovery/management.

`classify_reconciliation` is a pure function so the full case matrix
is unit-testable without a broker.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tradingbot.connector import BrokerOpenOrder, BrokerPosition, IBClient
from tradingbot.logging_setup import get_logger
from tradingbot.monitoring import KillSwitch
from tradingbot.persistence.enums import PositionSide, PositionState
from tradingbot.persistence.models import Position, ReconciliationLog
from tradingbot.persistence.repositories import PositionsRepository

_Clock = Callable[[], datetime]

_KILL_REASON: str = "startup_reconciliation_mismatch"


@dataclass(frozen=True)
class ReconciliationResult:
    """Outcome of a startup reconciliation pass."""

    clean: bool
    discrepancies: tuple[str, ...]


def classify_reconciliation(
    db_position: Position | None,
    broker_positions: Sequence[BrokerPosition],
    broker_orders: Sequence[BrokerOpenOrder],
) -> list[str]:
    """Return human-readable discrepancies; empty means the views agree.

    `broker_positions` must already exclude flat (zero-quantity)
    rows. The single-position invariant means the only clean shapes
    are: flat on both sides, or one position matching on both sides.
    """
    issues: list[str] = []

    if len(broker_positions) > 1:
        issues.append(
            f"broker holds {len(broker_positions)} positions; the "
            "single-position invariant allows at most one"
        )

    if db_position is None:
        for position in broker_positions:
            issues.append(
                f"broker holds {position.quantity} {position.symbol} but "
                "the bot tracks no open position"
            )
        if broker_orders and not broker_positions:
            symbols = ", ".join(sorted({o.symbol for o in broker_orders}))
            issues.append(
                f"broker has {len(broker_orders)} working order(s) "
                f"({symbols}) but the bot is flat"
            )
        return issues

    state = PositionState(db_position.state)
    if not broker_positions:
        issues.extend(_classify_db_only(db_position, state, broker_orders))
        return issues

    for position in broker_positions:
        issues.extend(_classify_matched(db_position, state, position))
    return issues


def _classify_db_only(
    db_position: Position,
    state: PositionState,
    broker_orders: Sequence[BrokerOpenOrder],
) -> list[str]:
    """Discrepancies when the bot has a position but the broker is flat."""
    has_working_order = any(
        o.symbol == db_position.symbol for o in broker_orders
    )
    if state in (PositionState.ABIERTA, PositionState.CERRANDO):
        suffix = " (a working order remains)" if has_working_order else ""
        return [
            f"the bot tracks an open {db_position.symbol} position in "
            f"{state.value} but the broker holds no position{suffix}"
        ]
    if state is PositionState.ABRIENDO and not has_working_order:
        # ABRIENDO with a live entry order is the normal "entry still
        # pending" shape and is clean; without one it is stuck.
        return [
            f"the bot has an ABRIENDO {db_position.symbol} entry but the "
            "broker has neither a position nor a working order for it"
        ]
    return []


def _classify_matched(
    db_position: Position,
    state: PositionState,
    broker_position: BrokerPosition,
) -> list[str]:
    """Discrepancies when both the bot and the broker hold a position."""
    if broker_position.symbol != db_position.symbol:
        return [
            f"broker holds {broker_position.symbol} but the bot tracks "
            f"{db_position.symbol}"
        ]
    if state is PositionState.ABRIENDO:
        return [
            f"{broker_position.symbol}: the broker holds the position but "
            "the bot is still in ABRIENDO — the entry filled while the "
            "bot was down"
        ]

    issues: list[str] = []
    db_side = PositionSide(db_position.side)
    broker_is_long = broker_position.quantity > 0
    if (db_side is PositionSide.LONG) != broker_is_long:
        broker_side = "long" if broker_is_long else "short"
        issues.append(
            f"{broker_position.symbol}: broker side ({broker_side}) "
            f"disagrees with the bot ({db_side.value})"
        )
    if abs(broker_position.quantity) != db_position.qty:
        issues.append(
            f"{broker_position.symbol}: broker quantity "
            f"{abs(broker_position.quantity)} disagrees with the bot's "
            f"{db_position.qty}"
        )
    return issues


def _db_view(db_position: Position | None) -> dict[str, Any]:
    if db_position is None:
        return {"open_position": None}
    return {
        "open_position": {
            "id": str(db_position.id),
            "symbol": db_position.symbol,
            "side": db_position.side,
            "qty": str(db_position.qty),
            "state": db_position.state,
        }
    }


def _broker_view(
    broker_positions: Sequence[BrokerPosition],
    broker_orders: Sequence[BrokerOpenOrder],
) -> dict[str, Any]:
    return {
        "positions": [
            {
                "symbol": p.symbol,
                "quantity": str(p.quantity),
                "avg_cost": str(p.avg_cost),
            }
            for p in broker_positions
        ],
        "open_orders": [
            {
                "ib_order_id": o.ib_order_id,
                "symbol": o.symbol,
                "action": o.action,
                "quantity": str(o.quantity),
            }
            for o in broker_orders
        ],
    }


class Reconciler:
    """Compare durable bot state against IBKR before trading starts."""

    def __init__(
        self,
        *,
        ib_client: IBClient,
        positions_repo: PositionsRepository,
        session_factory: async_sessionmaker[AsyncSession],
        kill_switch: KillSwitch,
        clock: _Clock = lambda: datetime.now(UTC),
    ) -> None:
        self._ib = ib_client
        self._positions = positions_repo
        self._sessions = session_factory
        self._kill = kill_switch
        self._clock = clock
        self._log = get_logger(__name__)

    async def reconcile_startup(self) -> ReconciliationResult:
        """Reconcile once at startup; trip the kill switch on any mismatch.

        The caller must ensure IBKR is connected first — `get_positions`
        / `get_open_orders` return empty when disconnected, which would
        be misread as "broker flat".
        """
        broker_positions = await self._ib.get_positions()
        broker_orders = await self._ib.get_open_orders()
        db_position = await self._positions.get_open_position()

        discrepancies = classify_reconciliation(
            db_position, broker_positions, broker_orders
        )
        await self._persist(
            db_position, broker_positions, broker_orders, discrepancies
        )

        if discrepancies:
            self._kill.trip(_KILL_REASON)
            self._log.critical(
                "reconciliation_mismatch",
                count=len(discrepancies),
                discrepancies=discrepancies,
            )
        else:
            self._log.info(
                "reconciliation_clean",
                db_has_position=db_position is not None,
                broker_positions=len(broker_positions),
                broker_orders=len(broker_orders),
            )
        return ReconciliationResult(
            clean=not discrepancies, discrepancies=tuple(discrepancies)
        )

    async def _persist(
        self,
        db_position: Position | None,
        broker_positions: Sequence[BrokerPosition],
        broker_orders: Sequence[BrokerOpenOrder],
        discrepancies: list[str],
    ) -> None:
        async with self._sessions() as session:
            session.add(
                ReconciliationLog(
                    ts=self._clock(),
                    expected=_db_view(db_position),
                    actual=_broker_view(broker_positions, broker_orders),
                    discrepancies=(
                        {"items": discrepancies} if discrepancies else None
                    ),
                )
            )
            await session.commit()


__all__ = [
    "Reconciler",
    "ReconciliationResult",
    "classify_reconciliation",
]
