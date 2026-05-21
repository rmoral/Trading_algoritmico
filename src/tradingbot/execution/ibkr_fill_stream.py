"""`IBKRFillStream`: bridge `ib_insync` order events into the `FillHandler`.

`ib_insync` exposes order activity as two event streams we care about:

- `commissionReportEvent(trade, fill, report)` — fires once per
  execution, after IBKR has reported its commission. It carries the
  complete picture (execution + commission), so it is the single
  trigger we use for fills.
- `orderStatusEvent(trade)` — fires on every status change; we act
  only on cancellations/rejections so a never-filled entry releases
  the single-position slot.

This is the only place outside the order router and submitter that
touches `ib_insync`. The translated events (`BrokerFill`,
`BrokerOrderStatus`) keep the `FillHandler` broker-agnostic.

`ib_insync` invokes event callbacks synchronously on the bot's event
loop; the actual DB work is scheduled with `asyncio.create_task`. The
`FillHandler`'s internal lock serialises whatever order those tasks
run in.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from tradingbot.execution.fill_handler import (
    BrokerFill,
    BrokerOrderStatus,
    FillHandler,
)
from tradingbot.logging_setup import get_logger
from tradingbot.persistence.enums import OrderStatus

if TYPE_CHECKING:
    from tradingbot.connector.ib_client import IBClient

# `ib_insync` order-status strings we translate; every other status
# (Submitted, PreSubmitted, Filled, ...) is ignored here — fills are
# handled via `commissionReportEvent`.
_STATUS_MAP: dict[str, OrderStatus] = {
    "Cancelled": OrderStatus.CANCELLED,
    "ApiCancelled": OrderStatus.CANCELLED,
}


def _ensure_utc(value: datetime) -> datetime:
    """Normalise a broker timestamp to timezone-aware UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _safe_decimal(value: object) -> Decimal:
    """Convert a broker numeric to `Decimal`, mapping junk to 0.

    `ib_insync` can surface unset doubles or NaN; those become 0 so a
    bogus value never poisons a P&L calculation.
    """
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")
    if not result.is_finite():
        return Decimal("0")
    return result


class IBKRFillStream:
    """Subscribe to `ib_insync` order events and drive the `FillHandler`."""

    def __init__(self, ib_client: IBClient, fill_handler: FillHandler) -> None:
        self._client = ib_client
        self._handler = fill_handler
        self._tasks: set[asyncio.Task[None]] = set()
        self._started = False
        self._log = get_logger(__name__)

    def start(self) -> None:
        """Attach the event handlers. Idempotent."""
        if self._started:
            return
        ib = self._client.ib
        ib.commissionReportEvent += self._on_commission_report
        ib.orderStatusEvent += self._on_order_status
        self._started = True
        self._log.info("ibkr_fill_stream_started")

    def stop(self) -> None:
        """Detach the event handlers. Idempotent."""
        if not self._started:
            return
        ib = self._client.ib
        ib.commissionReportEvent -= self._on_commission_report
        ib.orderStatusEvent -= self._on_order_status
        self._started = False
        self._log.info("ibkr_fill_stream_stopped")

    def _on_commission_report(
        self, _trade: Any, fill: Any, report: Any
    ) -> None:
        try:
            execution = fill.execution
            broker_fill = BrokerFill(
                ib_order_id=int(execution.orderId),
                exec_id=str(execution.execId),
                ts=_ensure_utc(execution.time),
                qty=_safe_decimal(execution.shares),
                price=_safe_decimal(execution.price),
                commission=_safe_decimal(report.commission),
                exchange=str(execution.exchange) or None,
            )
        except (AttributeError, TypeError, ValueError) as exc:
            self._log.error(
                "ibkr_fill_stream_bad_commission_report",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return
        self._schedule(self._handler.on_fill(broker_fill))

    def _on_order_status(self, trade: Any) -> None:
        try:
            raw_status = str(trade.orderStatus.status)
            mapped = _STATUS_MAP.get(raw_status)
            if mapped is None:
                return
            broker_status = BrokerOrderStatus(
                ib_order_id=int(trade.order.orderId), status=mapped
            )
        except (AttributeError, TypeError, ValueError) as exc:
            self._log.error(
                "ibkr_fill_stream_bad_order_status",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return
        self._schedule(self._handler.on_order_status(broker_status))

    def _schedule(self, coro: Any) -> None:
        """Run a handler coroutine, keeping a strong reference to its task."""
        task: asyncio.Task[None] = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


__all__ = ["IBKRFillStream"]
