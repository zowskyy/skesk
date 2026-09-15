"""Vertical Slice 1: the complete lifecycle, end to end.

This test is the slice's reason for existing. Milestone 0 proved every subsystem
in isolation; this proves they compose, by answering one question:

    Can SkillKernel perform one complete research-to-skill workflow correctly
    through its core domain layer?

Two rules govern how it is written.

**Public APIs only.** The test never writes a registry index or a record file
directly. If it ever needs to, that is a missing capability to report, not an
obstacle to route around — hand-editing state would make the test prove nothing
about the API a real consumer would use.

**Reload before asserting.** State is re-read from disk through fresh store
objects before the provenance chain is checked, so the test proves persistence
rather than in-memory survival.

The subject is a deliberately trivial synthetic skill. The point is to prove the
lifecycle, not the usefulness of the example.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from skillkernel.core.errors import GateError
from skillkernel.core.paths import Layout
from skillkernel.evaluation.runner import evaluate_skill
from skillkernel.evaluation.suite import write_evaluation_suite
from skillkernel.evidence.ledger import EvidenceLedger
from skillkernel.experiments.store import ExperimentStore
from skillkernel.knowledge.store import KnowledgeStore
from skillkernel.project.bootstrap import initialize
from skillkernel.promotion.engine import PromotionEngine
from skillkernel.skills.store import SkillStore
from skillkernel.validation.provenance import verify_provenance

pytestmark = pytest.mark.acceptance

PROJECT = "lifecycle-demo"
ACTOR = "acceptance-test"


# --- the synthetic subject -------------------------------------------------
#
# A skill that applies when a task involves a flaky import ordering failure, and
# explicitly does not apply during an interactive debugging session. Trivial on
# purpose: its activation boundary is unambiguous, so a failure in this test
# means the lifecycle is broken rather than the subject being unclear.

SKILL_NAME = "Stabilise import ordering"
ACTIVATION = {
    "require_any": ["flaky-import-order"],
    "require_all": [],
    "exclude_any": ["interactive-debug"],
}
POSITIVE_CASES = [
    {"case_id": "pos-001", "signals": ["flaky-import-order"]},
    {"case_id": "pos-002", "signals": ["flaky-import-order", "ci-run"]},
    {"case_id": "pos-003", "signals": ["flaky-import-order", "test-failure"]},
]
NEGATIVE_CASES = [
    {"case_id": "neg-001", "signals": ["interactive-debug"]},
    {"case_id": "neg-002", "signals": ["flaky-import-order", "interactive-debug"]},
    {"case_id": "neg-003", "signals": ["unrelated-task"]},
]


def build_workspace(root: Path) -> Layout:
    """Step 1 — initialize a fresh workspace."""
    return initialize(root, project_name=PROJECT)


def record_knowledge(layout: Layout) -> str:
    """Step 2 — a claim about reality."""
    store = KnowledgeStore(layout)
    record = store.add(
        statement="Import order affects test outcomes when modules mutate global state at import time.",
        scope=f"project:{PROJECT}",
        source_type="project_observation",
    )
    return record.id


def run_experiment(layout: Layout) -> str:
    """Step 3 — define, freeze, then measure. Never the other way round."""
    store = ExperimentStore(layout)
    definition = store.add(
        title="Does pinning import order remove the flake?",
        hypothesis="Pinning import order eliminates the intermittent failure.",
        independent_variable="import order pinning",
        control={"label": "unpinned", "description": "imports in arbitrary order"},
        treatment={"label": "pinned", "description": "imports pinned deterministically"},
        corpus={
            "id": "flake-corpus-a",
            "description": "40 recorded CI runs of the affected suite",
            "cases": ["run-1", "run-2"],
        },
        scorer={"name": "pass-rate", "version": "1", "deterministic": True},
        primary_metric={"name": "pass_rate", "direction": "maximize", "description": None},
        pass_threshold=0.99,
        failure_threshold=0.80,
        project=PROJECT,
    )
    store.freeze(definition.id)
    result = store.record_result(
        definition.id,
        primary_metric_value=1.0,
        control_metrics={"pass_rate": 0.72},
        treatment_metrics={"pass_rate": 1.0},
        interpretation="Pinning import order removed the flake across the corpus.",
        started_at="2024-01-31T11:00:00Z",
    )
    assert result.verdict == "pass", "kernel should derive a passing verdict from frozen thresholds"
    return definition.id


def test_complete_lifecycle_from_empty_directory(tmp_path: Path, frozen_now: str) -> None:
    """The whole slice, in order, through public APIs only."""

    # 1. Initialize -----------------------------------------------------------
    layout = build_workspace(tmp_path)
    assert layout.config_file.is_file()
    assert layout.profile_file.is_file()

    # 2. Knowledge ------------------------------------------------------------
    knowledge_id = record_knowledge(layout)

    # 3. Experiment: define, freeze, measure -----------------------------------
    experiment_id = run_experiment(layout)

    # 4. Evidence -------------------------------------------------------------
    ledger = EvidenceLedger(layout)
    evidence = ledger.record(
        kind="experiment_run",
        summary="CI pass-rate measurements before and after pinning import order.",
        project=PROJECT,
        source_type="command",
        source_detail="pytest -q, 40 runs",
        artifact_bytes=b"unpinned: 29/40 passed\npinned: 40/40 passed\n",
        artifact_name="pass-rates.txt",
        media_type="text/plain",
        experiment=experiment_id,
        knowledge=[knowledge_id],
    )

    # 5. Skill candidate ------------------------------------------------------
    skills = SkillStore(layout)
    skill = skills.create(
        name=SKILL_NAME,
        scope="project",
        purpose="Pin import order so suites that mutate global state at import time run deterministically.",
        applies_when=["a test failure reproduces only under some import orders"],
        do_not_apply_when=["debugging interactively, where changing import order hides the fault"],
        activation_rules=ACTIVATION,
        created_from=[f"experiment:{experiment_id}"],
        origin_project=PROJECT,
    )
    assert skill.maturity == "observed"

    engine = PromotionEngine(layout)
    skill = engine.promote(skill.id, "candidate", reason="Applicability established.", actor=ACTOR)
    assert skill.maturity == "candidate"

    # 6. Experimental — requires a procedure and experiment evidence -----------
    skills.update(
        skill.id,
        procedure=[
            "Identify the modules that mutate global state at import time.",
            "Pin their import order in the suite's conftest.",
            "Re-run the suite to confirm the flake is gone.",
        ],
    )
    skills.attach_evidence(
        skill.id, experiments=[experiment_id], knowledge=[knowledge_id], records=[evidence.id]
    )
    skill = engine.promote(
        skill.id, "experimental", reason="Procedure and experiment evidence recorded.", actor=ACTOR
    )
    assert skill.maturity == "experimental"

    # 7. Evaluate activation boundaries ---------------------------------------
    write_evaluation_suite(
        layout,
        skill.id,
        corpus_id="activation-corpus-a",
        pass_threshold=1.0,
        max_false_activation_rate=0.0,
        positive=POSITIVE_CASES,
        negative=NEGATIVE_CASES,
    )
    report = evaluate_skill(layout, skill.id, project=PROJECT)

    assert report.verdict == "pass"
    # Reported separately, never as one aggregate (DEC-0003): a skill that fires
    # on everything must not be able to hide behind a single number.
    assert report.true_positives == 3
    assert report.true_negatives == 3
    assert report.false_activations == 0
    assert report.missed_activations == 0
    assert report.evidence_id is not None

    # 8. Promote to validated --------------------------------------------------
    skills.update(
        skill.id,
        success_conditions=["The suite passes on ten consecutive runs."],
        failure_modes=["Pinning hides a genuine ordering dependency that should be fixed instead."],
        verification=["Run the suite ten times and confirm no failures."],
    )
    skill = engine.promote(
        skill.id, "validated", reason="Evaluation passed on activation-corpus-a.", actor=ACTOR
    )
    assert skill.maturity == "validated"

    # 9. Reload from disk ------------------------------------------------------
    # Nothing below reuses an object from above: fresh Layout, fresh stores.
    reloaded_layout = Layout(root=tmp_path)
    reloaded_skills = SkillStore(reloaded_layout)
    reloaded = reloaded_skills.get(skill.id)

    assert reloaded.maturity == "validated"
    assert reloaded.name == SKILL_NAME
    assert reloaded.fingerprint() == skill.fingerprint()
    assert reloaded.procedure == skill.procedure

    # Activation still behaves after a round trip through disk.
    assert reloaded.applies_to(["flaky-import-order"])
    assert not reloaded.applies_to(["flaky-import-order", "interactive-debug"])

    # 10. Provenance -----------------------------------------------------------
    provenance = verify_provenance(reloaded_layout, skill.id)

    assert provenance.findings == [], "\n".join(provenance.findings)
    assert experiment_id in provenance.experiment_ids
    assert knowledge_id in provenance.knowledge_ids
    assert evidence.id in provenance.evidence_ids
    assert report.evidence_id in provenance.evidence_ids
    assert provenance.artifacts, "the chain must reach at least one hashed artifact"

    # Promotion history is contiguous and ends where the skill actually is.
    assert provenance.maturity_path == ["observed", "candidate", "experimental", "validated"]


def test_promotion_to_validated_is_refused_without_an_evaluation(
    tmp_path: Path, frozen_now: str
) -> None:
    """The gate is the point: reaching 'validated' must require evidence."""
    layout = build_workspace(tmp_path)
    knowledge_id = record_knowledge(layout)
    experiment_id = run_experiment(layout)

    skills = SkillStore(layout)
    engine = PromotionEngine(layout)
    skill = skills.create(
        name=SKILL_NAME,
        scope="project",
        purpose="Pin import order for determinism.",
        applies_when=["a flaky ordering failure"],
        do_not_apply_when=["interactive debugging"],
        activation_rules=ACTIVATION,
        created_from=[f"experiment:{experiment_id}"],
        origin_project=PROJECT,
    )
    engine.promote(skill.id, "candidate", reason="Applicability established.", actor=ACTOR)
    skills.update(skill.id, procedure=["Pin the import order."])
    skills.attach_evidence(skill.id, experiments=[experiment_id], knowledge=[knowledge_id])
    engine.promote(skill.id, "experimental", reason="Procedure recorded.", actor=ACTOR)

    skills.update(
        skill.id,
        success_conditions=["The suite passes."],
        failure_modes=["Hides a real ordering bug."],
        verification=["Re-run the suite."],
    )

    with pytest.raises(GateError) as excinfo:
        engine.promote(skill.id, "validated", reason="No evaluation was run.", actor=ACTOR)

    assert "evaluation" in str(excinfo.value).lower()

    # The refusal must not have half-applied: the skill stays where it was.
    assert SkillStore(Layout(root=tmp_path)).get(skill.id).maturity == "experimental"
