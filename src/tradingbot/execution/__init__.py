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
from tradingbot.execution.order_router import OrderRouter, OrderSubmissionError

__all__ = [
    "BracketSpec",
    "EntryLegSpec",
    "ExitLegSpec",
    "OrderRouter",
    "OrderSubmissionError",
    "build_bracket",
]
