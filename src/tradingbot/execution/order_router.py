"""Order router: signal -> bracket -> IBKR submission.

The router is the *only* module allowed to call IBKR for order
submission (CLAUDE.md §2 principle 3). The discovery strategy never
talks to the broker — it emits `TradingSignal`, the engine runs the
risk manager, and the router converts the approved signal into the
3-leg bracket the broker actually receives.

The router depends on:
- a `BracketSubmitter` (Protocol below) so tests can swap out
  `ib_insync` for a fake,
- an `OrderRepository` to persist every leg + state transition,
- the `RiskManager` to gate every entry submission.

`build_bracket` is the pure helper in `bracket.py`; the router runs
the impure work: side effects on Postgres + IBKR, error handling,
partial-submission rollback.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tradingbot.execution.bracket import (
    BracketSpec,
    EntryLegSpec,
    ExitLegSpec,
    build_bracket,
)
from tradingbot.logging_setup import get_logger
from tradingbot.persistence.enums import OrderStatus
from tradingbot.persistence.models import Order
from tradingbot.risk import (
    OrderRequest,
    RiskContext,
    RiskDecision,
    RiskManager,
)
from tradingbot.risk.types import RiskRefusalReason
from tradingbot.strategy.types import TradingSignal


class OrderSubmissionError(Exception):
    """Wraps any broker-level failure during bracket submission."""


@dataclass(frozen=True)
class SubmittedLeg:
    """What the broker returned after one leg was accepted."""

    internal_id: UUID
    ib_order_id: int


@dataclass(frozen=True)
class SubmittedBracket:
    """Three accepted IBKR order ids for one bracket."""

    entry: SubmittedLeg
    stop_loss: SubmittedLeg
    take_profit: SubmittedLeg


@runtime_checkable
class BracketSubmitter(Protocol):
    """Adapter over `ib_insync` so tests can substitute a fake."""

    async def submit_bracket(
        self, entry: EntryLegSpec, stop_loss: ExitLegSpec, take_profit: ExitLegSpec
    ) -> SubmittedBracket: ...


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RouterResult:
    """What a `submit_signal` call produced."""

    approved: bool
    decision: RiskDecision
    bracket: SubmittedBracket | None
    parent_internal_id: UUID | None


_Clock = Callable[[], datetime]


class OrderRouter:
    """Gate every entry through the risk manager, then submit the bracket."""

    def __init__(
        self,
        submitter: BracketSubmitter,
        risk_manager: RiskManager,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        signal_id_lookup: Callable[[TradingSignal], Awaitable[UUID | None]] | None = None,
        clock: _Clock = lambda: datetime.now(UTC),
    ) -> None:
        self._submitter = submitter
        self._risk = risk_manager
        self._sessions = session_factory
        self._lookup_signal_id = signal_id_lookup
        self._clock = clock
        self._log = get_logger(__name__)

    async def submit_signal(
        self, signal: TradingSignal, ctx: RiskContext
    ) -> RouterResult:
        """Approve the signal, build the bracket, submit it.

        Steps:
        1. Build the `BracketSpec` from the signal (pure).
        2. Build an `OrderRequest` for the parent + run RiskManager.
        3. If approved, submit through the broker adapter.
        4. Persist three `orders` rows in the DB (one per leg).

        Refusals return a `RouterResult(approved=False, decision=..)`
        with `bracket=None`. No partial state is left in the DB.
        """
        bracket = build_bracket(signal)
        request = self._signal_to_request(signal, bracket)
        decision = self._risk.approve(request, ctx)
        if not decision.approved:
            self._log.warning(
                "order_router_signal_refused",
                symbol=signal.symbol,
                side=signal.side.value,
                refusals=[r.value for r in decision.refusals],
            )
            return RouterResult(
                approved=False,
                decision=decision,
                bracket=None,
                parent_internal_id=None,
            )

        try:
            submitted = await self._submitter.submit_bracket(
                bracket.entry, bracket.stop_loss, bracket.take_profit
            )
        except Exception as exc:
            self._log.error(
                "order_router_broker_error",
                symbol=signal.symbol,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            raise OrderSubmissionError(str(exc)) from exc

        signal_id = (
            await self._lookup_signal_id(signal)
            if self._lookup_signal_id is not None
            else None
        )
        await self._persist_bracket(signal, bracket, submitted, signal_id)
        self._log.info(
            "order_router_bracket_submitted",
            symbol=signal.symbol,
            entry_ib_id=submitted.entry.ib_order_id,
            stop_ib_id=submitted.stop_loss.ib_order_id,
            tp_ib_id=submitted.take_profit.ib_order_id,
        )
        return RouterResult(
            approved=True,
            decision=decision,
            bracket=submitted,
            parent_internal_id=submitted.entry.internal_id,
        )

    def _signal_to_request(
        self, signal: TradingSignal, bracket: BracketSpec
    ) -> OrderRequest:
        """Build the `OrderRequest` the risk manager evaluates.

        We use `signal.entry_price` (the strategy's planned entry),
        NOT `bracket.entry.limit_price` (the slightly-offset price we
        send to the broker to ensure marketability). The R-multiple,
        stop-loss-pct, and commission-pct calculations must reflect
        the planned trade — the offset is a fill-probability nudge,
        not a risk parameter.
        """
        return OrderRequest(
            symbol=signal.symbol,
            side=signal.side,
            qty=signal.qty,
            order_type=bracket.entry.order_type,
            entry_price=signal.entry_price,
            stop_loss_price=signal.stop_loss_price,
            take_profit_price=signal.take_profit_price,
            estimated_commission_usd=signal.expected_commission_usd,
            bid=None,
            ask=None,
        )

    async def _persist_bracket(
        self,
        signal: TradingSignal,
        bracket: BracketSpec,
        submitted: SubmittedBracket,
        signal_id: UUID | None,
    ) -> None:
        now = self._clock()
        async with self._sessions() as db:
            db.add(
                Order(
                    id=submitted.entry.internal_id,
                    ib_order_id=submitted.entry.ib_order_id,
                    signal_id=signal_id,
                    parent_order_id=None,
                    ts_created=now,
                    ts_submitted=now,
                    symbol=bracket.entry.symbol,
                    side=bracket.entry.side.value,
                    qty=bracket.entry.qty,
                    order_type=bracket.entry.order_type.value,
                    limit_price=bracket.entry.limit_price,
                    stop_price=None,
                    time_in_force=bracket.entry.time_in_force.value,
                    status=OrderStatus.SUBMITTED.value,
                )
            )
            db.add(
                Order(
                    id=submitted.stop_loss.internal_id,
                    ib_order_id=submitted.stop_loss.ib_order_id,
                    signal_id=signal_id,
                    parent_order_id=submitted.entry.internal_id,
                    ts_created=now,
                    ts_submitted=now,
                    symbol=bracket.stop_loss.symbol,
                    side=bracket.stop_loss.side.value,
                    qty=bracket.stop_loss.qty,
                    order_type=bracket.stop_loss.order_type.value,
                    limit_price=None,
                    stop_price=bracket.stop_loss.stop_price,
                    time_in_force=bracket.stop_loss.time_in_force.value,
                    status=OrderStatus.SUBMITTED.value,
                )
            )
            db.add(
                Order(
                    id=submitted.take_profit.internal_id,
                    ib_order_id=submitted.take_profit.ib_order_id,
                    signal_id=signal_id,
                    parent_order_id=submitted.entry.internal_id,
                    ts_created=now,
                    ts_submitted=now,
                    symbol=bracket.take_profit.symbol,
                    side=bracket.take_profit.side.value,
                    qty=bracket.take_profit.qty,
                    order_type=bracket.take_profit.order_type.value,
                    limit_price=bracket.take_profit.limit_price,
                    stop_price=None,
                    time_in_force=bracket.take_profit.time_in_force.value,
                    status=OrderStatus.SUBMITTED.value,
                )
            )
            await db.commit()


def new_internal_id() -> UUID:
    """Helper for adapters that need to mint client-side ids."""
    return uuid4()


# Re-export so RiskRefusalReason is importable from this module too.
__all__ = [
    "BracketSubmitter",
    "OrderRouter",
    "OrderSubmissionError",
    "RiskRefusalReason",
    "RouterResult",
    "SubmittedBracket",
    "SubmittedLeg",
    "new_internal_id",
]
