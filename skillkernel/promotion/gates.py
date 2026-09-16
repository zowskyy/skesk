"""Maturity gates.

The state machine says which transitions are *shaped* correctly. The gates say
which are *earned*. A skill cannot reach ``validated`` because someone typed the
word; it reaches ``validated`` because a passing evaluation exists, in this
repository, for this version of this skill.

Requirements by target maturity:

``candidate``
    A purpose, and both halves of the activation boundary. ``do_not_apply_when``
    is required here rather than later because a skill nobody has bounded is a
    skill that will fire on everything.
``experimental``
    A procedure, and experiment evidence that resolves and is frozen with at
    least one recorded result.
``validated``
    Success conditions, failure modes, verification, and a passing evaluation
    whose ``skill_fingerprint`` matches the skill's current fingerprint.
``trusted``
    Passing evaluations across at least N distinct corpora. Repeated evidence
    from one corpus is repetition, not independent confirmation.
``deprecated``
    A stated reason.

Every gate also rejects any skill resting on refuted knowledge, at any maturity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from skillkernel.core.config import KernelConfig
from skillkernel.core.paths import Layout
from skillkernel.evidence.ledger import EvidenceLedger
from skillkernel.experiments.store import ExperimentStore
from skillkernel.knowledge.store import KnowledgeStore
from skillkernel.skills.model import SkillRecord

if TYPE_CHECKING:  # pragma: no cover - typing only
    from skillkernel.evidence.model import EvidenceRecord

__all__ = ["GateReport", "check_gate", "passing_evaluations"]

EVALUATION_KIND = "evaluation_report"


@dataclass
class GateReport:
    """Why a promotion is or is not allowed."""

    skill_id: str
    target: str
    reasons: list[str] = field(default_factory=list)
    evidence_used: list[str] = field(default_factory=list)
    corpora_used: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.reasons

    def to_document(self) -> dict[str, object]:
        return {
            "target": self.target,
            "passed": self.passed,
            "reasons": list(self.reasons),
            "evidence_used": list(self.evidence_used),
            "corpora_used": list(self.corpora_used),
        }


def _missing(skill: SkillRecord, field_name: str) -> bool:
    value = getattr(skill, field_name, None)
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return not value


def passing_evaluations(
    layout: Layout, skill: SkillRecord, *, current_only: bool = True
) -> list[EvidenceRecord]:
    """Evaluation evidence for this skill that passed.

    With ``current_only`` the fingerprint must match the skill as it stands, so
    an evaluation of a procedure that has since been rewritten does not count.
    """
    fingerprint = skill.fingerprint()
    found: list[EvidenceRecord] = []
    for record in EvidenceLedger(layout).for_skill(skill.id):
        if record.kind != EVALUATION_KIND:
            continue
        if str(record.attributes.get("verdict")) != "pass":
            continue
        if current_only and record.skill_fingerprint != fingerprint:
            continue
        found.append(record)
    return found


def _check_knowledge(layout: Layout, skill: SkillRecord, report: GateReport) -> None:
    store = KnowledgeStore(layout)
    for knowledge_id in skill.knowledge_ids:
        if not store.has(knowledge_id):
            report.reasons.append(f"cites knowledge {knowledge_id}, which does not exist")
            continue
        record = store.get(knowledge_id)
        if record.status == "refuted":
            report.reasons.append(
                f"rests on refuted knowledge {knowledge_id}: {record.statement[:80]}"
            )


def _check_candidate(skill: SkillRecord, report: GateReport) -> None:
    if _missing(skill, "purpose"):
        report.reasons.append("has no purpose")
    if _missing(skill, "applies_when"):
        report.reasons.append("has no applies_when entries")
    if _missing(skill, "do_not_apply_when"):
        report.reasons.append(
            "has no do_not_apply_when entries; a skill without a stated exclusion "
            "will activate where it should not"
        )
    if not skill.provenance.get("created_from"):
        report.reasons.append("has no provenance: created_from is empty")


def _check_experimental(layout: Layout, skill: SkillRecord, report: GateReport) -> None:
    if _missing(skill, "procedure"):
        report.reasons.append("has no procedure")
    if not skill.experiment_ids:
        report.reasons.append("cites no experiment evidence")
        return

    store = ExperimentStore(layout)
    for experiment_id in skill.experiment_ids:
        if not store.has(experiment_id):
            report.reasons.append(f"cites experiment {experiment_id}, which does not exist")
            continue
        definition = store.get(experiment_id)
        if not definition.frozen:
            report.reasons.append(f"cites experiment {experiment_id}, which is not frozen")
            continue
        if not store.results(experiment_id):
            report.reasons.append(f"cites experiment {experiment_id}, which has no recorded result")


def _check_validated(layout: Layout, skill: SkillRecord, report: GateReport) -> None:
    for field_name, label in (
        ("success_conditions", "success conditions"),
        ("failure_modes", "failure modes"),
        ("verification", "verification steps"),
    ):
        if _missing(skill, field_name):
            report.reasons.append(f"has no {label}")

    current = passing_evaluations(layout, skill, current_only=True)
    if current:
        report.evidence_used.extend(record.id for record in current)
        report.corpora_used.extend(
            sorted({str(record.attributes.get("corpus_id")) for record in current})
        )
        return

    stale = passing_evaluations(layout, skill, current_only=False)
    if stale:
        report.reasons.append(
            "has no passing evaluation for its current behaviour; the evaluations on record "
            f"({', '.join(record.id for record in stale)}) were run against a different version "
            "of this skill. Re-evaluate after changing a skill's procedure or activation rules."
        )
    else:
        report.reasons.append(
            "has no passing evaluation; run evaluate_skill() before promoting to validated"
        )


def _check_trusted(
    layout: Layout, skill: SkillRecord, config: KernelConfig, report: GateReport
) -> None:
    current = passing_evaluations(layout, skill, current_only=True)
    corpora = sorted({str(record.attributes.get("corpus_id")) for record in current})
    report.evidence_used.extend(record.id for record in current)
    report.corpora_used.extend(corpora)
    required = config.min_trusted_distinct_corpora
    if len(corpora) < required:
        report.reasons.append(
            f"has passing evaluations across {len(corpora)} distinct corpus/corpora "
            f"({', '.join(corpora) or 'none'}), but 'trusted' requires {required}. "
            "Repeated evidence from one corpus is repetition, not independent confirmation."
        )


def _check_deprecated(skill: SkillRecord, report: GateReport) -> None:
    deprecation = skill.deprecation or {}
    if not str(deprecation.get("reason", "")).strip():
        report.reasons.append("is being deprecated without a stated reason")


def check_gate(
    layout: Layout, skill: SkillRecord, target: str, *, config: KernelConfig
) -> GateReport:
    """Evaluate the gate for ``target``. An empty ``reasons`` list means allowed."""
    report = GateReport(skill_id=skill.id, target=target)

    # Refuted knowledge disqualifies at every maturity, not only on the way up.
    _check_knowledge(layout, skill, report)

    if target == "candidate":
        _check_candidate(skill, report)
    elif target == "experimental":
        _check_candidate(skill, report)
        _check_experimental(layout, skill, report)
    elif target == "validated":
        _check_candidate(skill, report)
        _check_experimental(layout, skill, report)
        _check_validated(layout, skill, report)
    elif target == "trusted":
        _check_candidate(skill, report)
        _check_experimental(layout, skill, report)
        _check_validated(layout, skill, report)
        _check_trusted(layout, skill, config, report)
    elif target == "deprecated":
        _check_deprecated(skill, report)

    report.corpora_used = sorted(set(report.corpora_used))
    report.evidence_used = sorted(set(report.evidence_used))
    return report
