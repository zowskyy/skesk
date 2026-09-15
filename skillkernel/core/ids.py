"""Stable identifiers.

Identifiers are ``PREFIX-NNNN`` with at least four digits. They are allocated
from a monotonic counter held in each registry index, never from ``max(existing)
+ 1``: tombstoned or deleted records must not free their number for reuse,
because provenance chains outlive the records they point at.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from skillkernel.core.errors import ValidationError

__all__ = [
    "DECISION",
    "EVIDENCE",
    "EXPERIMENT",
    "KNOWLEDGE",
    "OBSERVATION",
    "SKILL",
    "RecordId",
    "format_id",
    "is_valid_id",
    "parse_id",
    "sequence_of",
]

SKILL = "SKILL"
KNOWLEDGE = "K"
EXPERIMENT = "EXP"
EVIDENCE = "EV"
DECISION = "DEC"
OBSERVATION = "OBS"

ALL_PREFIXES = (SKILL, KNOWLEDGE, EXPERIMENT, EVIDENCE, DECISION, OBSERVATION)

_PATTERN = re.compile(r"^(?P<prefix>[A-Z]+)-(?P<sequence>\d{4,})$")
_WIDTH = 4


@dataclass(frozen=True)
class RecordId:
    prefix: str
    sequence: int

    def __str__(self) -> str:
        return format_id(self.prefix, self.sequence)


def format_id(prefix: str, sequence: int) -> str:
    """Render ``prefix`` and ``sequence`` as a zero-padded identifier."""
    if sequence < 1:
        raise ValueError(f"identifier sequence must be >= 1, got {sequence}")
    return f"{prefix}-{sequence:0{_WIDTH}d}"


def parse_id(value: str, expected_prefix: str | None = None) -> RecordId:
    """Parse ``value`` into a :class:`RecordId`, raising on malformed input."""
    if not isinstance(value, str):
        raise ValidationError(f"identifier must be a string, got {type(value).__name__}")
    match = _PATTERN.match(value)
    if match is None:
        raise ValidationError(f"malformed identifier {value!r}; expected e.g. SKILL-0001")
    prefix = match.group("prefix")
    if expected_prefix is not None and prefix != expected_prefix:
        raise ValidationError(f"expected a {expected_prefix}-* identifier, got {value!r}")
    return RecordId(prefix=prefix, sequence=int(match.group("sequence")))


def is_valid_id(value: object, expected_prefix: str | None = None) -> bool:
    if not isinstance(value, str):
        return False
    match = _PATTERN.match(value)
    if match is None:
        return False
    if expected_prefix is not None and match.group("prefix") != expected_prefix:
        return False
    return int(match.group("sequence")) >= 1


def sequence_of(value: str) -> int:
    return parse_id(value).sequence
