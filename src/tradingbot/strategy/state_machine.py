"""Position-state-machine transition validator (CLAUDE.md §6).

```
CERRADA   <-> ABRIENDO       (discovery -> entry order submitted)
ABRIENDO   -> ABIERTA        (entry filled)
ABRIENDO   -> CERRADA        (entry cancelled / rejected before any fill)
ABIERTA    -> CERRANDO       (exit order submitted, by SL/TP/flatten)
CERRANDO   -> CERRADA        (exit filled)
CERRANDO   -> ABIERTA        (exit cancelled mid-flight; the position is
                              still open and management resumes)
```

Pure: there is no shared state here, just the directed graph of
allowed transitions. The strategy engine consumes this before
writing the new state to Postgres.
"""

from __future__ import annotations

from tradingbot.persistence.enums import PositionState

# Directed graph of legal transitions. Every edge that the strategy
# engine may ever execute MUST appear here.
ALLOWED_TRANSITIONS: dict[PositionState, frozenset[PositionState]] = {
    PositionState.CERRADA: frozenset({PositionState.ABRIENDO}),
    PositionState.ABRIENDO: frozenset(
        {PositionState.ABIERTA, PositionState.CERRADA}
    ),
    PositionState.ABIERTA: frozenset({PositionState.CERRANDO}),
    PositionState.CERRANDO: frozenset(
        {PositionState.CERRADA, PositionState.ABIERTA}
    ),
}


class InvalidTransitionError(Exception):
    """Raised when the strategy engine attempts an unauthorized state move.

    Hitting this would mean a logic bug somewhere in CAPA 4 — never
    a transient runtime condition. It is caught at the top of the
    strategy loop, logged, and the bot trips its kill switch
    (CLAUDE.md §2 principle 4: the single-position invariant is
    sacred).
    """

    def __init__(self, from_state: PositionState, to_state: PositionState) -> None:
        super().__init__(
            f"cannot transition {from_state.value} -> {to_state.value}"
        )
        self.from_state = from_state
        self.to_state = to_state


def can_transition(from_state: PositionState, to_state: PositionState) -> bool:
    """True iff `from_state -> to_state` is in `ALLOWED_TRANSITIONS`."""
    return to_state in ALLOWED_TRANSITIONS.get(from_state, frozenset())


def assert_transition(from_state: PositionState, to_state: PositionState) -> None:
    """Raise `InvalidTransitionError` when the transition is not allowed."""
    if not can_transition(from_state, to_state):
        raise InvalidTransitionError(from_state, to_state)
