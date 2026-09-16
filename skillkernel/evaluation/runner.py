"""Running an evaluation and recording its result as evidence.

The runner is what turns a score into something the promotion gates can trust.
It writes a deterministic JSON report, records it in the evidence ledger, and
stamps the report with the skill's **behaviour fingerprint** at the moment of
evaluation.

That fingerprint is the whole point. It lets the ``validated`` gate ask a
question it could not otherwise ask: *is this evaluation still about this
skill?* Edit the procedure or the activation rules afterwards and the
fingerprint changes, so the stale evaluation stops counting.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from skillkernel.core.clock import now_iso
from skillkernel.core.paths import Layout
from skillkernel.evaluation.scorer import ScoreBreakdown, score_activation
from skillkernel.evaluation.suite import EvaluationSuite, load_evaluation_suite
from skillkernel.evidence.ledger import EvidenceLedger

__all__ = ["EVALUATION_EVIDENCE_KIND", "EvaluationReport", "evaluate_skill"]

EVALUATION_EVIDENCE_KIND = "evaluation_report"
REPORT_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class EvaluationReport:
    skill_id: str
    corpus_id: str
    verdict: str
    breakdown: ScoreBreakdown
    skill_fingerprint: str
    evaluated_at: str
    pass_threshold: float
    max_false_activation_rate: float
    failure_reasons: tuple[str, ...]
    evidence_id: str | None

    # Counts are surfaced individually rather than behind the breakdown so that
    # callers and tests read them as four separate facts (DEC-0003).
    @property
    def true_positives(self) -> int:
        return self.breakdown.true_positives

    @property
    def true_negatives(self) -> int:
        return self.breakdown.true_negatives

    @property
    def false_activations(self) -> int:
        return self.breakdown.false_activations

    @property
    def missed_activations(self) -> int:
        return self.breakdown.missed_activations

    @property
    def accuracy(self) -> float:
        return self.breakdown.accuracy

    @property
    def passed(self) -> bool:
        return self.verdict == "pass"

    def to_document(self) -> dict[str, Any]:
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "skill": self.skill_id,
            "corpus_id": self.corpus_id,
            "scorer": "activation-boundary",
            "evaluated_at": self.evaluated_at,
            "skill_fingerprint": self.skill_fingerprint,
            "thresholds": {
                "pass_threshold": self.pass_threshold,
                "max_false_activation_rate": self.max_false_activation_rate,
            },
            "verdict": self.verdict,
            "failure_reasons": list(self.failure_reasons),
            "results": self.breakdown.to_document(),
        }

    def render_json(self) -> bytes:
        """Deterministic serialization: same inputs, byte-identical output."""
        return json.dumps(self.to_document(), indent=2, sort_keys=True).encode("utf-8") + b"\n"


def derive_verdict(
    breakdown: ScoreBreakdown, suite: EvaluationSuite
) -> tuple[str, tuple[str, ...]]:
    """Derive pass/fail from the frozen thresholds. The caller never supplies it."""
    reasons: list[str] = []
    if breakdown.total == 0:
        reasons.append("no cases were scored")
    if breakdown.accuracy < suite.pass_threshold:
        reasons.append(
            f"accuracy {breakdown.accuracy:.3f} is below the pass threshold "
            f"{suite.pass_threshold:.3f}"
        )
    if breakdown.false_activation_rate > suite.max_false_activation_rate:
        reasons.append(
            f"false activation rate {breakdown.false_activation_rate:.3f} exceeds the guardrail "
            f"{suite.max_false_activation_rate:.3f} "
            f"({breakdown.false_activations} of {breakdown.negative_total} negative cases)"
        )
    return ("fail" if reasons else "pass"), tuple(reasons)


def evaluate_skill(
    layout: Layout,
    skill_id: str,
    *,
    project: str,
    record_evidence: bool = True,
    now: str | None = None,
) -> EvaluationReport:
    """Evaluate a skill's activation boundaries and record the result."""
    from skillkernel.skills.store import SkillStore  # local import avoids a cycle

    skill = SkillStore(layout).require(skill_id)
    suite = load_evaluation_suite(layout, skill_id)
    breakdown = score_activation(skill, suite.cases)
    verdict, reasons = derive_verdict(breakdown, suite)

    report = EvaluationReport(
        skill_id=skill_id,
        corpus_id=suite.corpus_id,
        verdict=verdict,
        breakdown=breakdown,
        skill_fingerprint=skill.fingerprint(),
        evaluated_at=now or now_iso(),
        pass_threshold=suite.pass_threshold,
        max_false_activation_rate=suite.max_false_activation_rate,
        failure_reasons=reasons,
        evidence_id=None,
    )
    if not record_evidence:
        return report

    evidence = EvidenceLedger(layout).record(
        kind=EVALUATION_EVIDENCE_KIND,
        summary=(
            f"Activation-boundary evaluation of {skill_id} on corpus {suite.corpus_id}: {verdict}"
        ),
        project=project,
        source_type="evaluation",
        source_detail=f"activation-boundary scorer v{suite.scorer_version}",
        artifact_bytes=report.render_json(),
        artifact_name=f"evaluation-{suite.corpus_id}.json",
        media_type="application/json",
        skill=skill_id,
        skill_fingerprint=report.skill_fingerprint,
        attributes={
            "verdict": verdict,
            "corpus_id": suite.corpus_id,
            "accuracy": round(breakdown.accuracy, 6),
            "true_positives": breakdown.true_positives,
            "true_negatives": breakdown.true_negatives,
            "false_activations": breakdown.false_activations,
            "missed_activations": breakdown.missed_activations,
        },
        now=report.evaluated_at,
    )

    return EvaluationReport(
        skill_id=report.skill_id,
        corpus_id=report.corpus_id,
        verdict=report.verdict,
        breakdown=report.breakdown,
        skill_fingerprint=report.skill_fingerprint,
        evaluated_at=report.evaluated_at,
        pass_threshold=report.pass_threshold,
        max_false_activation_rate=report.max_false_activation_rate,
        failure_reasons=report.failure_reasons,
        evidence_id=evidence.id,
    )
