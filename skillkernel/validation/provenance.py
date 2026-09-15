"""Provenance verification.

Answers, for one skill: *does everything this skill rests on actually exist, and
is it still what it was?* The walk is

    skill -> evidence -> experiment -> knowledge -> artifact

and the reverse direction is checked at the same time: every evidence record the
skill cites must point back at a resolvable experiment, knowledge record and
artifact, so the chain can be read from either end.

A clean report means a reader can start at a trusted skill and reach the bytes
that justify it, or start at an artifact and find what was concluded from it.
That round trip is the property the whole system exists to provide, so it is
checked rather than assumed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from skillkernel.core.errors import SkillKernelError
from skillkernel.core.paths import Layout
from skillkernel.evidence.ledger import EvidenceLedger
from skillkernel.experiments.store import ExperimentStore
from skillkernel.knowledge.store import KnowledgeStore
from skillkernel.skills.history import history_issues, maturity_path
from skillkernel.skills.store import SkillStore

__all__ = ["ProvenanceReport", "verify_provenance"]


@dataclass
class ProvenanceReport:
    """Everything a skill rests on, and every way the chain is broken."""

    skill_id: str
    findings: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    experiment_ids: list[str] = field(default_factory=list)
    knowledge_ids: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    maturity_path: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings

    def summary(self) -> str:
        if self.ok:
            return (
                f"{self.skill_id}: provenance intact "
                f"({len(self.evidence_ids)} evidence, {len(self.experiment_ids)} experiment(s), "
                f"{len(self.knowledge_ids)} knowledge record(s), {len(self.artifacts)} artifact(s))"
            )
        return f"{self.skill_id}: {len(self.findings)} provenance finding(s)"


def verify_provenance(layout: Layout, skill_id: str) -> ProvenanceReport:
    """Walk and verify a skill's complete provenance chain."""
    report = ProvenanceReport(skill_id=skill_id)
    skills = SkillStore(layout)
    ledger = EvidenceLedger(layout)
    experiments = ExperimentStore(layout)
    knowledge = KnowledgeStore(layout)

    try:
        skill = skills.require(skill_id)
    except SkillKernelError as exc:
        report.findings.append(str(exc))
        return report

    # --- promotion history -------------------------------------------------
    try:
        history = skills.history(skill_id)
    except SkillKernelError as exc:
        report.findings.append(f"promotion history unreadable: {exc}")
    else:
        report.maturity_path = maturity_path(history)
        report.findings.extend(
            history_issues(history, skill_id=skill_id, current_maturity=skill.maturity)
        )

    # --- knowledge ----------------------------------------------------------
    for knowledge_id in skill.knowledge_ids:
        if not knowledge.has(knowledge_id):
            report.findings.append(f"cites knowledge {knowledge_id}, which does not exist")
            continue
        report.knowledge_ids.append(knowledge_id)
        claim = knowledge.get(knowledge_id)
        if claim.status == "refuted":
            report.findings.append(f"rests on refuted knowledge {knowledge_id}")
        elif claim.status == "superseded":
            report.findings.append(
                f"rests on superseded knowledge {knowledge_id}; "
                f"its successor is {claim.superseded_by}"
            )

    # --- experiments --------------------------------------------------------
    for experiment_id in skill.experiment_ids:
        if not experiments.has(experiment_id):
            report.findings.append(f"cites experiment {experiment_id}, which does not exist")
            continue
        report.experiment_ids.append(experiment_id)
        definition = experiments.get(experiment_id)
        if not definition.frozen:
            report.findings.append(f"cites experiment {experiment_id}, which is not frozen")
        elif not definition.hash_matches():
            report.findings.append(
                f"cites experiment {experiment_id}, which was edited after it was frozen"
            )
        if not experiments.results(experiment_id):
            report.findings.append(
                f"cites experiment {experiment_id}, which has no recorded result"
            )

    # --- evidence: those cited, plus those recorded against this skill ------
    cited = set(skill.evidence_ids)
    attached = {record.id for record in ledger.for_skill(skill_id)}
    for evidence_id in sorted(cited | attached):
        if not ledger.has(evidence_id):
            report.findings.append(f"cites evidence {evidence_id}, which does not exist")
            continue
        report.evidence_ids.append(evidence_id)
        evidence = ledger.get(evidence_id)

        # Reverse direction: the evidence must resolve back to its own links.
        if evidence.experiment_id and not experiments.has(evidence.experiment_id):
            report.findings.append(
                f"evidence {evidence_id} links to experiment {evidence.experiment_id}, "
                "which does not exist"
            )
        for linked_knowledge in evidence.knowledge_ids:
            if not knowledge.has(linked_knowledge):
                report.findings.append(
                    f"evidence {evidence_id} links to knowledge {linked_knowledge}, "
                    "which does not exist"
                )
        if evidence.artifact is not None:
            report.artifacts.append(str(evidence.artifact["path"]))

    # --- ledger integrity over the records this chain depends on ------------
    relevant_evidence = set(report.evidence_ids)
    for ledger_finding in ledger.verify():
        if ledger_finding.evidence_id in relevant_evidence:
            report.findings.append(f"evidence {ledger_finding}")

    relevant_experiments = set(report.experiment_ids)
    for experiment_finding in experiments.verify():
        if experiment_finding.experiment_id in relevant_experiments:
            report.findings.append(f"experiment {experiment_finding}")

    report.evidence_ids = sorted(set(report.evidence_ids))
    report.experiment_ids = sorted(set(report.experiment_ids))
    report.knowledge_ids = sorted(set(report.knowledge_ids))
    report.artifacts = sorted(set(report.artifacts))
    return report
