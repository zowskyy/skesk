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

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from skillkernel.core.config import KernelConfig
from skillkernel.core.errors import SkillKernelError
from skillkernel.core.paths import Layout
from skillkernel.evaluation.runner import CORPUS_DIGEST_ATTRIBUTE, INPUT_DIGEST_ATTRIBUTE
from skillkernel.evaluation.suite import (
    EvaluationInputs,
    corpus_content_digest,
    evaluation_input_digest,
)
from skillkernel.evidence.ledger import EvidenceLedger
from skillkernel.experiments.store import ExperimentStore
from skillkernel.knowledge.store import KnowledgeStore
from skillkernel.skills.model import SkillRecord
from skillkernel.utils.hashing import sha256_file

if TYPE_CHECKING:  # pragma: no cover - typing only
    from skillkernel.evidence.model import EvidenceRecord

__all__ = [
    "EVIDENCE_BACKED_MATURITIES",
    "GateReport",
    "check_gate",
    "current_input_digest",
    "passing_evaluations",
    "verifiable_evaluations",
]

EVALUATION_KIND = "evaluation_report"

EVIDENCE_BACKED_MATURITIES = ("validated", "trusted")
"""Maturities whose standing rests on evaluation evidence."""


def current_input_digest(layout: Layout, skill_id: str) -> str | None:
    """The digest of the inputs this skill would be evaluated against now.

    ``None`` when it cannot be computed -- a missing, malformed or empty suite.
    That is not an error here: it is the absence of proof, and every caller
    treats it as *not current* rather than skipping the check.
    """
    try:
        return evaluation_input_digest(layout, skill_id)
    except SkillKernelError:
        return None


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
    digest = current_input_digest(layout, skill.id) if current_only else None
    found: list[EvidenceRecord] = []
    for record in EvidenceLedger(layout).for_skill(skill.id):
        if record.kind != EVALUATION_KIND:
            continue
        if str(record.attributes.get("verdict")) != "pass":
            continue
        if not current_only:
            found.append(record)
            continue
        if record.skill_fingerprint != fingerprint:
            continue
        # The second dimension. A matching fingerprint says the evaluation is
        # still about this skill; it says nothing about what it was measured
        # against, which is how a deleted guardrail corpus kept counting.
        recorded = record.attributes.get(INPUT_DIGEST_ATTRIBUTE)
        if not recorded or digest is None or str(recorded) != digest:
            continue
        found.append(record)
    return found


def verifiable_evaluations(layout: Layout, skill: SkillRecord) -> list[EvidenceRecord]:
    """Passing evaluations that can still be checked against their own inputs.

    ``validated`` asks about now: does evidence exist for this skill against the
    inputs on disk today. ``trusted`` asks about accumulated history: has this
    been independently confirmed across genuinely distinct corpora. A skill has
    one live suite, so demanding that every counted evaluation match it made the
    second question unanswerable -- and before the snapshot existed, the only
    way to answer it was to count evidence nobody could check.

    So this does not ask whether an evaluation is live. It asks whether it is
    still *provable*: the snapshot it was written with is present, survives the
    ledger's artifact verification, parses, and re-derives the very corpus digest
    the record claims. A digest string on its own proves nothing; it is the
    preserved inputs that do.

    The skill fingerprint requirement is unchanged: evidence about behaviour the
    skill no longer has is not confirmation of the behaviour it has now.
    """
    fingerprint = skill.fingerprint()
    found: list[EvidenceRecord] = []
    for record in EvidenceLedger(layout).for_skill(skill.id):
        if record.kind != EVALUATION_KIND:
            continue
        if str(record.attributes.get("verdict")) != "pass":
            continue
        if record.skill_fingerprint != fingerprint:
            continue
        claimed = record.attributes.get(CORPUS_DIGEST_ATTRIBUTE)
        if not claimed:
            # Pre-snapshot evidence. Immutable history, never proof.
            continue
        if _replayed_corpus_digest(layout, record) != str(claimed):
            continue
        found.append(record)
    return found


def _replayed_corpus_digest(layout: Layout, record: EvidenceRecord) -> str | None:
    """Re-derive corpus identity from the evaluation's preserved snapshot."""
    if record.artifact is None:
        return None
    path = layout.root / str(record.artifact["path"])
    if not path.is_file():
        return None
    try:
        if sha256_file(path) != str(record.artifact["sha256"]):
            # The ledger already calls this corruption; it is never evidence.
            return None
        document = json.loads(path.read_text(encoding="utf-8"))
        snapshot = document["inputs"]
        inputs = EvaluationInputs.from_snapshot(snapshot, source=record.id)
        return corpus_content_digest(inputs)
    except (OSError, ValueError, KeyError, TypeError, SkillKernelError):
        return None


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
    # Distinctness is measured over preserved scoring content, not over labels:
    # rewriting corpus_id must never manufacture a second corpus, and the same
    # cases re-filed under new names must never count twice.
    current = verifiable_evaluations(layout, skill)
    corpora = sorted({str(record.attributes[CORPUS_DIGEST_ATTRIBUTE]) for record in current})
    report.evidence_used.extend(record.id for record in current)
    report.corpora_used.extend(corpora)
    required = config.min_trusted_distinct_corpora
    if len(corpora) < required:
        report.reasons.append(
            f"has independently verifiable passing evaluations across {len(corpora)} "
            f"distinct corpus/corpora, but 'trusted' requires {required}. "
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
