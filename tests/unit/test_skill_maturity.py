"""The maturity state machine, tested exhaustively.

Every ordered pair of states is checked: the table's legal transitions are
asserted legal, and *all* others are asserted rejected. A state machine tested
only on its happy path is a state machine with unknown edges.
"""

from __future__ import annotations

from itertools import product

import pytest
from skillkernel.core.errors import TransitionError
from skillkernel.skills.maturity import (
    ACTIVE_MATURITIES,
    MATURITIES,
    at_least,
    can_transition,
    check_transition,
    transitions_from,
)

LEGAL = {
    ("observed", "candidate"),
    ("candidate", "experimental"),
    ("candidate", "deprecated"),
    ("experimental", "validated"),
    ("experimental", "deprecated"),
    ("validated", "trusted"),
    ("validated", "deprecated"),
    ("trusted", "deprecated"),
}


@pytest.mark.parametrize(("current", "target"), sorted(LEGAL))
def test_every_legal_transition_is_permitted(current: str, target: str) -> None:
    assert can_transition(current, target)
    check_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    sorted(pair for pair in product(MATURITIES, repeat=2) if pair not in LEGAL),
)
def test_every_other_transition_is_rejected(current: str, target: str) -> None:
    assert not can_transition(current, target)
    with pytest.raises(TransitionError):
        check_transition(current, target)


def test_the_ladder_cannot_be_skipped() -> None:
    with pytest.raises(TransitionError, match="not a permitted maturity transition"):
        check_transition("candidate", "validated")
    with pytest.raises(TransitionError):
        check_transition("observed", "trusted")


def test_an_observed_skill_cannot_be_deprecated() -> None:
    """It has asserted nothing a consumer could have relied on."""
    with pytest.raises(TransitionError):
        check_transition("observed", "deprecated")


def test_deprecated_is_terminal() -> None:
    assert transitions_from("deprecated") == frozenset()
    for target in MATURITIES:
        assert not can_transition("deprecated", target)


def test_maturity_cannot_transition_to_itself() -> None:
    for state in MATURITIES:
        with pytest.raises(TransitionError):
            check_transition(state, state)


def test_a_self_transition_says_so_explicitly() -> None:
    with pytest.raises(TransitionError, match="already 'validated'"):
        check_transition("validated", "validated")


def test_an_unknown_state_is_rejected_rather_than_treated_as_new() -> None:
    with pytest.raises(TransitionError, match="unknown maturity"):
        transitions_from("legendary")
    with pytest.raises(TransitionError, match="unknown target maturity"):
        check_transition("validated", "legendary")


def test_the_error_message_names_the_reachable_states() -> None:
    with pytest.raises(TransitionError, match="you may reach: deprecated, experimental"):
        check_transition("candidate", "trusted")


def test_the_active_ladder_excludes_deprecated() -> None:
    assert "deprecated" not in ACTIVE_MATURITIES
    assert ACTIVE_MATURITIES == ("observed", "candidate", "experimental", "validated", "trusted")


@pytest.mark.parametrize(
    ("state", "minimum", "expected"),
    [
        ("validated", "experimental", True),
        ("validated", "validated", True),
        ("candidate", "validated", False),
        ("trusted", "observed", True),
        ("observed", "candidate", False),
    ],
)
def test_at_least_orders_the_active_ladder(state: str, minimum: str, expected: bool) -> None:
    assert at_least(state, minimum) is expected


def test_deprecated_is_never_at_least_an_active_state() -> None:
    """Deprecated is off the ladder, not at the top or bottom of it."""
    for minimum in ACTIVE_MATURITIES:
        assert at_least("deprecated", minimum) is False
    assert at_least("deprecated", "deprecated") is True


def test_a_trusted_skill_is_not_considered_deprecated() -> None:
    assert at_least("trusted", "deprecated") is False
