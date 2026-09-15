"""The activation-boundary scorer.

Scores a skill's ``activation_rules`` against labelled cases. Deterministic and
model-free: the same skill and the same cases always produce the same numbers,
which is what makes the result usable as evidence.

The four outcome classes are reported separately and never collapsed into a
single number (DEC-0003):

    true positive      fired, and should have
    true negative      did not fire, and should not have
    false activation   fired when it should not have   <- the dangerous one
    missed activation  did not fire when it should have

An aggregate score would let a skill that fires on everything look excellent: it
would score 100% recall while being actively harmful. ``accuracy`` is computed
as a convenience for the pass threshold, but the false-activation rate is a
*separate* guardrail precisely so that accuracy cannot mask over-activation.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from skillkernel.evaluation.suite import EvaluationCase
from skillkernel.skills.model import SkillRecord

__all__ = ["CaseOutcome", "ScoreBreakdown", "score_activation"]

TRUE_POSITIVE = "true_positive"
TRUE_NEGATIVE = "true_negative"
FALSE_ACTIVATION = "false_activation"
MISSED_ACTIVATION = "missed_activation"


@dataclass(frozen=True)
class CaseOutcome:
    """What the skill did on one case, and what it should have done."""

    case_id: str
    expected: str
    activated: bool
    outcome: str

    @property
    def correct(self) -> bool:
        return self.outcome in (TRUE_POSITIVE, TRUE_NEGATIVE)

    def to_document(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "expected": self.expected,
            "activated": self.activated,
            "outcome": self.outcome,
        }


@dataclass(frozen=True)
class ScoreBreakdown:
    true_positives: int
    true_negatives: int
    false_activations: int
    missed_activations: int
    outcomes: tuple[CaseOutcome, ...]

    @property
    def total(self) -> int:
        return len(self.outcomes)

    @property
    def correct(self) -> int:
        return self.true_positives + self.true_negatives

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0

    @property
    def negative_total(self) -> int:
        return self.true_negatives + self.false_activations

    @property
    def positive_total(self) -> int:
        return self.true_positives + self.missed_activations

    @property
    def false_activation_rate(self) -> float:
        """Share of negative cases the skill wrongly fired on."""
        return self.false_activations / self.negative_total if self.negative_total else 0.0

    @property
    def missed_activation_rate(self) -> float:
        return self.missed_activations / self.positive_total if self.positive_total else 0.0

    def to_document(self) -> dict[str, Any]:
        return {
            "true_positives": self.true_positives,
            "true_negatives": self.true_negatives,
            "false_activations": self.false_activations,
            "missed_activations": self.missed_activations,
            "total_cases": self.total,
            "accuracy": round(self.accuracy, 6),
            "false_activation_rate": round(self.false_activation_rate, 6),
            "missed_activation_rate": round(self.missed_activation_rate, 6),
            "cases": [outcome.to_document() for outcome in self.outcomes],
        }


def classify(expected_applies: bool, activated: bool) -> str:
    if expected_applies:
        return TRUE_POSITIVE if activated else MISSED_ACTIVATION
    return FALSE_ACTIVATION if activated else TRUE_NEGATIVE


def score_activation(skill: SkillRecord, cases: Sequence[EvaluationCase]) -> ScoreBreakdown:
    """Run every case against the skill's activation rules."""
    outcomes: list[CaseOutcome] = []
    counts = {TRUE_POSITIVE: 0, TRUE_NEGATIVE: 0, FALSE_ACTIVATION: 0, MISSED_ACTIVATION: 0}

    for case in sorted(cases, key=lambda item: item.case_id):
        activated = skill.applies_to(case.signals)
        outcome = classify(case.should_apply, activated)
        counts[outcome] += 1
        outcomes.append(
            CaseOutcome(
                case_id=case.case_id,
                expected=case.expected,
                activated=activated,
                outcome=outcome,
            )
        )

    return ScoreBreakdown(
        true_positives=counts[TRUE_POSITIVE],
        true_negatives=counts[TRUE_NEGATIVE],
        false_activations=counts[FALSE_ACTIVATION],
        missed_activations=counts[MISSED_ACTIVATION],
        outcomes=tuple(outcomes),
    )
