"""Skills: procedures, with activation boundaries, evidence and a lifecycle.

This package currently provides the skill *contract* (:mod:`skillkernel.skills.model`)
and the maturity *state machine* (:mod:`skillkernel.skills.maturity`).

There is deliberately no ``SkillStore`` yet. Skills are directory-backed rather
than single-file records, so their storage layer has requirements — promotion
history, generated documents, example fixtures — that no existing code needs.
It will be written when the first vertical slice demands it, and not before:
an abstraction invented ahead of its first caller is a guess.
"""

from skillkernel.skills.maturity import MATURITIES, can_transition, transitions_from
from skillkernel.skills.model import SKILL_SCHEMA, SkillRecord

__all__ = [
    "MATURITIES",
    "SKILL_SCHEMA",
    "SkillRecord",
    "can_transition",
    "transitions_from",
]
