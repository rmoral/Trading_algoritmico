"""`IBKRBracketSubmitter`: production `BracketSubmitter` over `ib_insync`.

A bracket on IBKR is three orders submitted with explicit
parent/child references and an OCA group on the children so that a
fill of either the stop or the take-profit cancels the other.

This module is the only place outside the order router that talks
to IBKR for order submission. The strategy never sees `ib_insync`.

Mapping of our domain enums onto IBKR strings:

| `OrderType`  | `ib_insync` `Order(orderType=...)` |
|--------------|------------------------------------|
| `LIMIT`      | `"LMT"`                            |
| `STOP`       | `"STP"`                            |
| `STOP_LIMIT` | `"STP LMT"`                        |
| `MARKET`     | `"MKT"`                            |

| `OrderSide`  | `Order(action=...)` |
|--------------|---------------------|
| `BUY`        | `"BUY"`             |
| `SELL`       | `"SELL"`            |

| `TimeInForce`| `Order(tif=...)`    |
|--------------|---------------------|
| `DAY`        | `"DAY"`             |
| `IOC`        | `"IOC"`             |
| `GTC`        | `"GTC"`             |
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

from tradingbot.execution.bracket import EntryLegSpec, ExitLegSpec
from tradingbot.execution.order_router import SubmittedBracket, SubmittedLeg
from tradingbot.logging_setup import get_logger
from tradingbot.persistence.enums import OrderType, TimeInForce

if TYPE_CHECKING:
    from tradingbot.connector.ib_client import IBClient

_ORDER_TYPE_TO_IBKR: dict[OrderType, str] = {
    OrderType.LIMIT: "LMT",
    OrderType.STOP: "STP",
    OrderType.STOP_LIMIT: "STP LMT",
    OrderType.MARKET: "MKT",
}

_TIF_TO_IBKR: dict[TimeInForce, str] = {
    TimeInForce.DAY: "DAY",
    TimeInForce.IOC: "IOC",
    TimeInForce.GTC: "GTC",
}


class IBKRBracketSubmitter:
    """Implements `BracketSubmitter` against `ib_insync`."""

    def __init__(self, ib_client: IBClient) -> None:
        self._client = ib_client
        self._log = get_logger(__name__)

    async def submit_bracket(
        self,
        entry: EntryLegSpec,
        stop_loss: ExitLegSpec,
        take_profit: ExitLegSpec,
    ) -> SubmittedBracket:
        """Submit parent + two attached children in one call.

        - parent `transmit=False` so IBKR holds it.
        - children set `parentId` to the parent's id and join the
          same `ocaGroup` with `ocaType=1` (cancel-with-block on
          fill).
        - The last child submitted has `transmit=True`, which
          atomically releases the parent + both children to the
          market.

        The trio's internal ids (UUIDs) are minted here so the
        OrderRouter can persist with `orders.id`s that match the
        broker round-trip.
        """
        from ib_insync import LimitOrder, Stock, StopOrder

        ib = self._client.ib  # underlying IB-like

        contract = Stock(entry.symbol, exchange="SMART", currency="USD")
        oca_group = f"oca-{uuid4().hex[:8]}"

        # Parent: a marketable LIMIT.
        parent: Any = LimitOrder(
            action=entry.side.value,
            totalQuantity=float(entry.qty),
            lmtPrice=float(entry.limit_price),
        )
        parent.tif = _TIF_TO_IBKR[entry.time_in_force]
        parent.transmit = False

        # Stop-loss child.
        sl: Any
        if stop_loss.stop_price is None:
            raise ValueError("stop_loss leg must carry a stop_price")
        sl = StopOrder(
            action=stop_loss.side.value,
            totalQuantity=float(stop_loss.qty),
            stopPrice=float(stop_loss.stop_price),
        )
        sl.orderType = _ORDER_TYPE_TO_IBKR[stop_loss.order_type]
        sl.tif = _TIF_TO_IBKR[stop_loss.time_in_force]
        sl.ocaGroup = oca_group
        sl.ocaType = 1  # cancel with block on fill
        sl.transmit = False

        # Take-profit child (final order in the trio -> transmit=True).
        if take_profit.limit_price is None:
            raise ValueError("take_profit leg must carry a limit_price")
        tp: Any = LimitOrder(
            action=take_profit.side.value,
            totalQuantity=float(take_profit.qty),
            lmtPrice=float(take_profit.limit_price),
        )
        tp.tif = _TIF_TO_IBKR[take_profit.time_in_force]
        tp.ocaGroup = oca_group
        tp.ocaType = 1
        tp.transmit = True

        # ib_insync's `placeOrder` returns a `Trade`. After it
        # returns, `trade.order.orderId` is the IBKR-assigned id.
        parent_trade = ib.placeOrder(contract, parent)
        sl.parentId = parent_trade.order.orderId
        tp.parentId = parent_trade.order.orderId
        sl_trade = ib.placeOrder(contract, sl)
        tp_trade = ib.placeOrder(contract, tp)

        self._log.info(
            "ibkr_bracket_submitted",
            symbol=entry.symbol,
            side=entry.side.value,
            qty=str(entry.qty),
            parent_id=parent_trade.order.orderId,
            sl_id=sl_trade.order.orderId,
            tp_id=tp_trade.order.orderId,
            oca_group=oca_group,
        )

        return SubmittedBracket(
            entry=SubmittedLeg(
                internal_id=uuid4(),
                ib_order_id=int(parent_trade.order.orderId),
            ),
            stop_loss=SubmittedLeg(
                internal_id=uuid4(),
                ib_order_id=int(sl_trade.order.orderId),
            ),
            take_profit=SubmittedLeg(
                internal_id=uuid4(),
                ib_order_id=int(tp_trade.order.orderId),
            ),
        )


__all__ = ["IBKRBracketSubmitter"]
