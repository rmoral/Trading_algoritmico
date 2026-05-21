"""IBKR client wrapper with reconnect and heartbeat.

Wraps `ib_insync.IB` with:
- exponential-backoff connect loop (1 s, 2 s, ..., capped at 60 s),
- a `run()` task that keeps the connection alive across gateway
  restarts and emits a structured heartbeat log on a configurable
  interval,
- a Prometheus gauge `tradingbot_ib_connection_state` that reflects
  connection state at any moment,
- a thin `get_account_summary()` accessor.

This module is the only place strategy/execution code is allowed to
touch `ib_insync`. Everything else uses `IBClient`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol, runtime_checkable

from prometheus_client import Gauge

from tradingbot.logging_setup import get_logger
from tradingbot.settings import Settings

ConnectionListener = Callable[[bool], Awaitable[None]]


@dataclass(frozen=True)
class BrokerPosition:
    """A position IBKR reports for the account.

    `quantity` is signed: positive = long, negative = short.
    """

    symbol: str
    quantity: Decimal
    avg_cost: Decimal


@dataclass(frozen=True)
class BrokerOpenOrder:
    """A still-working order IBKR reports for the account."""

    ib_order_id: int
    symbol: str
    action: str  # "BUY" / "SELL"
    quantity: Decimal


def _to_decimal(value: object) -> Decimal:
    """Best-effort conversion of an `ib_insync` numeric to `Decimal`."""
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")
    return result if result.is_finite() else Decimal("0")

INITIAL_BACKOFF_SECONDS: float = 1.0
MAX_BACKOFF_SECONDS: float = 60.0
DEFAULT_HEARTBEAT_SECONDS: float = 5.0
DEFAULT_CONNECT_TIMEOUT_SECONDS: float = 10.0

CONNECTION_STATE: Gauge = Gauge(
    "tradingbot_ib_connection_state",
    "1 if the IBKR gateway is currently connected, 0 otherwise.",
)


@runtime_checkable
class IBLike(Protocol):
    """Subset of `ib_insync.IB` we depend on, for testability.

    The `*Event` attributes are `ib_insync.Event` objects: callbacks
    register with `+=` and unregister with `-=`. They are typed
    `Any` because `ib_insync` is an optional import (see
    `_default_ib`).
    """

    # Order-lifecycle event streams. Names mirror `ib_insync.IB`.
    commissionReportEvent: Any  # noqa: N815
    orderStatusEvent: Any  # noqa: N815

    def isConnected(self) -> bool: ...

    async def connectAsync(
        self,
        host: str,
        port: int,
        clientId: int,
        timeout: float = ...,
        readonly: bool = ...,
        account: str = ...,
    ) -> Any: ...

    def disconnect(self) -> None: ...

    async def accountSummaryAsync(self, account: str = ...) -> list[Any]: ...

    def placeOrder(self, contract: Any, order: Any) -> Any: ...

    def cancelOrder(self, order: Any) -> Any: ...

    def trades(self) -> list[Any]: ...

    def reqMktData(self, contract: Any) -> Any: ...

    def cancelMktData(self, contract: Any) -> None: ...

    # Declared as plain methods returning an Awaitable (not `async
    # def`) so they accept `ib_insync`'s wider `Awaitable[...]` return
    # annotation rather than requiring an exact `Coroutine[...]`.
    def reqPositionsAsync(self) -> Awaitable[list[Any]]: ...

    def reqOpenOrdersAsync(self) -> Awaitable[list[Any]]: ...


class IBClient:
    """High-level IBKR client.

    Owns the connection, the reconnect loop, and the heartbeat task.
    Inject a custom `ib` to substitute the underlying transport in
    tests; defaults to a fresh `ib_insync.IB()`.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        ib: IBLike | None = None,
        heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
        connect_timeout_seconds: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
        on_connection_change: ConnectionListener | None = None,
    ) -> None:
        self._settings = settings
        self._ib: IBLike = ib if ib is not None else _default_ib()
        self._heartbeat_seconds = heartbeat_seconds
        self._connect_timeout_seconds = connect_timeout_seconds
        self._on_connection_change = on_connection_change
        self._stop_event = asyncio.Event()
        self._log = get_logger(__name__)
        CONNECTION_STATE.set(0)

    async def _notify(self, connected: bool) -> None:
        if self._on_connection_change is None:
            return
        try:
            await self._on_connection_change(connected)
        except Exception as exc:  # noqa: BLE001 - listener must not break the loop
            self._log.warning(
                "ib_connection_listener_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )

    @property
    def ib(self) -> IBLike:
        """Underlying transport, exposed for advanced callers."""
        return self._ib

    def is_connected(self) -> bool:
        return self._ib.isConnected()

    async def connect(self) -> None:
        """Connect to IB Gateway with exponential-backoff retries.

        Returns once a connection is established or `stop()` has been
        called.
        """
        backoff = INITIAL_BACKOFF_SECONDS
        while not self._stop_event.is_set():
            try:
                await self._ib.connectAsync(
                    self._settings.ibkr_host,
                    self._settings.ibkr_port,
                    clientId=self._settings.ibkr_client_id,
                    timeout=self._connect_timeout_seconds,
                )
                CONNECTION_STATE.set(1)
                self._log.info(
                    "ib_connected",
                    host=self._settings.ibkr_host,
                    port=self._settings.ibkr_port,
                    client_id=self._settings.ibkr_client_id,
                    is_live=self._settings.is_live,
                )
                await self._notify(connected=True)
                return
            except (ConnectionRefusedError, TimeoutError, OSError) as exc:
                CONNECTION_STATE.set(0)
                self._log.warning(
                    "ib_connect_failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                    backoff_seconds=backoff,
                )
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=backoff)
                    return
                except TimeoutError:
                    pass
                backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)

    def disconnect(self) -> None:
        """Close the connection synchronously. Safe to call when not connected."""
        if self._ib.isConnected():
            self._ib.disconnect()
        CONNECTION_STATE.set(0)

    def stop(self) -> None:
        """Signal `run()` and `connect()` to exit at the next checkpoint."""
        self._stop_event.set()

    async def run(self) -> None:
        """Connect and keep the connection alive until `stop()` is called.

        On disconnect, the connect loop restarts. On every heartbeat
        interval while connected, emits a structured log line.
        Connection transitions fire `on_connection_change(connected)`
        if a listener is registered.
        """
        while not self._stop_event.is_set():
            await self.connect()
            if self._stop_event.is_set():
                break
            await self._heartbeat_until_disconnect()
            CONNECTION_STATE.set(0)
            await self._notify(connected=False)
            if not self._stop_event.is_set():
                self._log.warning("ib_disconnected_reconnecting")

    async def _heartbeat_until_disconnect(self) -> None:
        while self._ib.isConnected() and not self._stop_event.is_set():
            self._log.info("ib_heartbeat", connected=True)
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self._heartbeat_seconds
                )
                return
            except TimeoutError:
                continue

    async def get_account_summary(self) -> dict[str, str]:
        """Return the account summary as a dict of {tag: value}.

        Uses `ib_insync`'s ASYNC accessor. The synchronous
        `accountSummary()` internally calls `run_until_complete`,
        which raises `RuntimeError: This event loop is already
        running` when invoked from inside our asyncio context — the
        bot is async end-to-end, so the async accessor is mandatory.

        Returns an empty dict when not connected.
        """
        if not self._ib.isConnected():
            return {}
        rows = await self._ib.accountSummaryAsync()
        return {row.tag: row.value for row in rows}

    async def get_positions(self) -> list[BrokerPosition]:
        """Return IBKR's view of the account's open positions.

        Positions reported flat (`quantity == 0`) are dropped. Returns
        an empty list when not connected — callers that need to
        distinguish "flat" from "unknown" must check `is_connected()`
        first (startup reconciliation does).
        """
        if not self._ib.isConnected():
            return []
        rows = await self._ib.reqPositionsAsync()
        positions: list[BrokerPosition] = []
        for row in rows:
            quantity = _to_decimal(row.position)
            if quantity == 0:
                continue
            positions.append(
                BrokerPosition(
                    symbol=str(row.contract.symbol),
                    quantity=quantity,
                    avg_cost=_to_decimal(row.avgCost),
                )
            )
        return positions

    async def get_open_orders(self) -> list[BrokerOpenOrder]:
        """Return IBKR's view of the account's still-working orders.

        Empty when not connected (see `get_positions`).
        """
        if not self._ib.isConnected():
            return []
        trades = await self._ib.reqOpenOrdersAsync()
        return [
            BrokerOpenOrder(
                ib_order_id=int(trade.order.orderId),
                symbol=str(trade.contract.symbol),
                action=str(trade.order.action),
                quantity=_to_decimal(trade.order.totalQuantity),
            )
            for trade in trades
        ]


def _default_ib() -> IBLike:
    """Construct the real `ib_insync.IB` instance.

    Isolated in a function so importing this module without
    `ib_insync` available (e.g. in some test contexts) does not blow
    up at import time.
    """
    from ib_insync import IB  # imported lazily

    return IB()  # type: ignore[no-untyped-call]
