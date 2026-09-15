"""The skill maturity state machine.

    observed -> candidate -> experimental -> validated -> trusted

plus a terminal ``deprecated`` reachable from ``candidate`` onwards. There is no
path back from ``deprecated``, no skipping of intermediate states, and no
transition that is not in this table. "Arbitrary state mutation" is not a style
problem: it is how a skill nobody evaluated ends up marked trusted.

Note that ``observed -> deprecated`` is deliberately absent. An observed skill
has no claims to withdraw; it has not yet asserted anything a consumer could
have relied on.
"""

from __future__ import annotations

from types import MappingProxyType

from skillkernel.core.errors import TransitionError

__all__ = [
    "ACTIVE_MATURITIES",
    "MATURITIES",
    "MATURITY_ORDER",
    "can_transition",
    "check_transition",
    "transitions_from",
]

MATURITIES: tuple[str, ...] = (
    "observed",
    "candidate",
    "experimental",
    "validated",
    "trusted",
    "deprecated",
)

ACTIVE_MATURITIES: tuple[str, ...] = MATURITIES[:-1]

MATURITY_ORDER: dict[str, int] = {name: index for index, name in enumerate(ACTIVE_MATURITIES)}

_TRANSITIONS: dict[str, frozenset[str]] = {
    "observed": frozenset({"candidate"}),
    "candidate": frozenset({"experimental", "deprecated"}),
    "experimental": frozenset({"validated", "deprecated"}),
    "validated": frozenset({"trusted", "deprecated"}),
    "trusted": frozenset({"deprecated"}),
    "deprecated": frozenset(),
}

TRANSITIONS = MappingProxyType(_TRANSITIONS)


def transitions_from(state: str) -> frozenset[str]:
    """Return the states reachable in one step from ``state``."""
    if state not in _TRANSITIONS:
        raise TransitionError(
            f"unknown maturity {state!r}; expected one of {', '.join(MATURITIES)}"
        )
    return _TRANSITIONS[state]


def can_transition(current: str, target: str) -> bool:
    return target in transitions_from(current)


def check_transition(current: str, target: str) -> None:
    """Raise :class:`TransitionError` unless ``current -> target`` is allowed."""
    if target not in MATURITIES:
        raise TransitionError(
            f"unknown target maturity {target!r}; expected one of {', '.join(MATURITIES)}"
        )
    allowed = transitions_from(current)
    if target in allowed:
        return
    if current == target:
        raise TransitionError(f"skill is already {current!r}; a transition must change state")
    reachable = ", ".join(sorted(allowed)) if allowed else "nothing (terminal state)"
    raise TransitionError(
        f"{current!r} -> {target!r} is not a permitted maturity transition; "
        f"from {current!r} you may reach: {reachable}"
    )


def at_least(state: str, minimum: str) -> bool:
    """True when ``state`` is at or beyond ``minimum`` on the active ladder.

    ``deprecated`` is off the ladder and is never "at least" anything.
    """
    if state == "deprecated" or minimum == "deprecated":
        return state == minimum
    return MATURITY_ORDER[state] >= MATURITY_ORDER[minimum]
