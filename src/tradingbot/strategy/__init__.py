"""Strategy layer (CAPA 4).

The state machine governs which strategy is active at any time
(CLAUDE.md §6). In CERRADA the discovery strategy runs; in
ABIERTA the management strategy is responsible. Transitions
between states are durable: the strategy engine writes them to
Postgres BEFORE the action that triggers them.

This package only contains the pure pieces:
- `TradingSignal` and sizing types.
- `state_machine.can_transition` / `assert_transition`.
- `sizing.size_trade`: CLAUDE.md §6 sizing math.
- `discovery.evaluate_entry`: trigger detection + sizing wrapper.

The `StrategyEngine` loop that actually persists state transitions
and submits orders lives in a follow-up commit alongside the order
router (CAPA 3).
"""

from tradingbot.strategy.discovery import (
    DiscoveryConfig,
    evaluate_entry,
    is_rebound_bar,
    is_rejection_bar,
)
from tradingbot.strategy.sizing import (
    SizingConfig,
    SizingRefusal,
    SizingResult,
    estimate_round_trip_commission,
    size_trade,
)
from tradingbot.strategy.state_machine import (
    ALLOWED_TRANSITIONS,
    InvalidTransitionError,
    assert_transition,
    can_transition,
)
from tradingbot.strategy.types import TradingSignal

__all__ = [
    "ALLOWED_TRANSITIONS",
    "DiscoveryConfig",
    "InvalidTransitionError",
    "SizingConfig",
    "SizingRefusal",
    "SizingResult",
    "TradingSignal",
    "assert_transition",
    "can_transition",
    "estimate_round_trip_commission",
    "evaluate_entry",
    "is_rebound_bar",
    "is_rejection_bar",
    "size_trade",
]
