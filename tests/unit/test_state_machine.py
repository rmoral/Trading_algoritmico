"""Tests for the position state-machine transition validator."""

from __future__ import annotations

import pytest

from tradingbot.persistence.enums import PositionState
from tradingbot.strategy import (
    ALLOWED_TRANSITIONS,
    InvalidTransitionError,
    assert_transition,
    can_transition,
)


@pytest.mark.parametrize(
    "from_state,to_state",
    [
        (PositionState.CERRADA, PositionState.ABRIENDO),
        (PositionState.ABRIENDO, PositionState.ABIERTA),
        (PositionState.ABRIENDO, PositionState.CERRADA),
        (PositionState.ABIERTA, PositionState.CERRANDO),
        (PositionState.CERRANDO, PositionState.CERRADA),
        (PositionState.CERRANDO, PositionState.ABIERTA),
    ],
)
def test_allowed_transitions(
    from_state: PositionState, to_state: PositionState
) -> None:
    assert can_transition(from_state, to_state) is True
    # And the strict variant accepts them too.
    assert_transition(from_state, to_state)


@pytest.mark.parametrize(
    "from_state,to_state",
    [
        # No self-loops.
        (PositionState.CERRADA, PositionState.CERRADA),
        (PositionState.ABIERTA, PositionState.ABIERTA),
        # No skipping ABRIENDO.
        (PositionState.CERRADA, PositionState.ABIERTA),
        (PositionState.CERRADA, PositionState.CERRANDO),
        # No backward moves.
        (PositionState.ABIERTA, PositionState.ABRIENDO),
        (PositionState.ABIERTA, PositionState.CERRADA),
        (PositionState.CERRANDO, PositionState.ABRIENDO),
    ],
)
def test_disallowed_transitions(
    from_state: PositionState, to_state: PositionState
) -> None:
    assert can_transition(from_state, to_state) is False
    with pytest.raises(InvalidTransitionError) as exc_info:
        assert_transition(from_state, to_state)
    assert exc_info.value.from_state == from_state
    assert exc_info.value.to_state == to_state


def test_every_state_has_an_entry_in_the_graph() -> None:
    """The graph must contain every PositionState so unknown source
    states surface immediately as a refusal rather than as silent
    'no transitions'.
    """
    for state in PositionState:
        assert state in ALLOWED_TRANSITIONS, f"missing source {state}"


def test_no_transition_targets_unknown_state() -> None:
    targets: set[PositionState] = set()
    for dests in ALLOWED_TRANSITIONS.values():
        targets.update(dests)
    assert targets.issubset(set(PositionState))
