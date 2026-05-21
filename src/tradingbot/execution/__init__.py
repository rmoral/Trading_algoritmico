"""Execution layer (CAPA 3).

The order router is the only module allowed to talk to IBKR for
order submission. Strategy code emits a `TradingSignal`; the router
- builds the bracket (parent marketable limit + child stop + child
  take-profit) per CLAUDE.md §6,
- submits the parent + children atomically through the IBKR client,
- persists `orders` rows with their state transitions,
- waits for fills and reconciles back into `positions` + `fills`.

`build_bracket` is a pure helper that produces the three
`OrderRequest`-equivalent specs given a `TradingSignal` + the
configured per-trade tunables. The risk manager runs against the
parent before submission.
"""

from tradingbot.execution.bracket import (
    BracketSpec,
    EntryLegSpec,
    ExitLegSpec,
    build_bracket,
)
from tradingbot.execution.entry_timeout import EntryTimeoutWatcher, OrderCanceller
from tradingbot.execution.eod_flatten import EndOfDayFlattener, FlattenExecutor
from tradingbot.execution.equity_monitor import EquityMonitor
from tradingbot.execution.equity_tracker import EquityTracker
from tradingbot.execution.fill_handler import (
    BrokerFill,
    BrokerOrderStatus,
    FillHandler,
)
from tradingbot.execution.halt_monitor import HaltMonitor
from tradingbot.execution.halt_state import HaltStateStore
from tradingbot.execution.ibkr_fill_stream import IBKRFillStream
from tradingbot.execution.market_clock import MarketClock
from tradingbot.execution.order_rate import OrderRateCounter
from tradingbot.execution.order_router import OrderRouter, OrderSubmissionError

__all__ = [
    "BracketSpec",
    "BrokerFill",
    "BrokerOrderStatus",
    "EndOfDayFlattener",
    "EntryLegSpec",
    "EntryTimeoutWatcher",
    "EquityMonitor",
    "EquityTracker",
    "ExitLegSpec",
    "FillHandler",
    "FlattenExecutor",
    "HaltMonitor",
    "HaltStateStore",
    "IBKRFillStream",
    "MarketClock",
    "OrderCanceller",
    "OrderRateCounter",
    "OrderRouter",
    "OrderSubmissionError",
    "build_bracket",
]
