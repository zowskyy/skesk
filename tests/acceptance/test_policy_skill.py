"""The universal agent escalation policy, run through the proven lifecycle.

Phase D of Vertical Slice 1. The synthetic fixture proved the mechanism; this is
the mechanism's first real consumer.

The discipline, stated so it is visible in the test rather than only in a
commit message: **the lifecycle is not modified to make this policy pass.** If
the policy exposes a gap, the gap is classified first — lifecycle, evaluator, or
policy — and the classification is recorded before anything changes.

The subject is the two-method escalation rule from
``docs/policies/universal-agent-rules.md`` (DEC-0008). Its activation boundary
is the interesting part and the reason it is worth encoding as a skill rather
than as prose: it must fire when an agent is genuinely blocked, and must *not*
fire during ordinary work that happens to be difficult. Over-activation would
turn every hard task into an escalation.

It arrives at ``observed``, reaches ``candidate`` through the ordinary gate, and
goes no further than the locally earned evidence carries it — exactly like any
other skill (DEC-0004 as amended by DEC-0015). That applies to a policy we are
confident in just as much as to one we are not: confidence is not evidence.
"""

from __future__ import annotations

from pathlib import Path

import pytest
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

PROJECT = "universal-core"
ACTOR = "policy-validation"

# The escalation rule fires on a genuine blocker. "blocked" and
# "second-method-failed" are the states that warrant stopping; the exclusions
# are the states that emphatically do not.
ACTIVATION_RULES = {
    "require_any": ["blocked", "second-method-failed", "ambiguous-requirement"],
    "require_all": [],
    "exclude_any": ["routine-progress", "first-attempt-underway"],
}

# Cases where an agent genuinely should stop and ask.
POSITIVE_CASES = [
    {
        "case_id": "blocked-after-two-methods",
        "signals": ["blocked", "second-method-failed"],
        "description": "Two materially different approaches have failed.",
    },
    {
        "case_id": "ambiguous-requirement",
        "signals": ["ambiguous-requirement"],
        "description": "Competing readings would produce materially different systems.",
    },
    {
        "case_id": "blocked-on-credentials",
        "signals": ["blocked"],
        "description": "An external authorization the agent cannot grant itself.",
    },
]

# Cases where escalating would be wrong. These are the ones that matter: an
# escalation rule that fires on ordinary difficulty is worse than none, because
# it trains the user to ignore it.
NEGATIVE_CASES = [
    {
        "case_id": "routine-work",
        "signals": ["routine-progress"],
        "description": "Ordinary implementation proceeding normally.",
    },
    {
        "case_id": "first-attempt-still-running",
        "signals": ["first-attempt-underway"],
        "description": "Method 1 has not concluded; the budget is not spent.",
    },
    {
        "case_id": "hard-but-progressing",
        "signals": ["routine-progress", "difficult-task"],
        "description": "Difficulty is not a blocker.",
    },
    {
        "case_id": "blocked-but-first-attempt-underway",
        "signals": ["blocked", "first-attempt-underway"],
        "description": "Exclusions win: investigate before escalating.",
    },
]

PROCEDURE = [
    "Investigate the failure until you can state why it happened.",
    "Attempt Method 1: a coherent technical approach.",
    "If it fails, diagnose why, record the evidence, and choose a materially different Method 2.",
    "If Method 2 fails, STOP. Do not attempt a third method.",
    "Report: problem, observed failure, both methods and why each failed, current "
    "repository state, diagnosis separating known from likely from unknown, and the "
    "smallest question that unblocks you.",
    "Wait for direction before proceeding.",
]


def test_the_escalation_policy_passes_the_same_lifecycle(tmp_path: Path, frozen_now: str) -> None:
    layout = initialize(tmp_path / "core", project_name=PROJECT)

    # Knowledge: the claim the policy rests on.
    knowledge = KnowledgeStore(layout)
    claim = knowledge.add(
        statement=(
            "Repeated variations of one failed approach do not increase the chance of "
            "success; they consume budget that a materially different approach would use."
        ),
        scope="universal",
        source_type="project_observation",
    )

    # Experiment: the evidence the claim rests on.
    experiments = ExperimentStore(layout)
    definition = experiments.add(
        title="Does a two-method budget reduce wasted effort?",
        hypothesis=(
            "Capping autonomous recovery at two materially different methods reduces "
            "wasted attempts without reducing resolution rate."
        ),
        independent_variable="autonomous recovery budget",
        control={"label": "uncapped", "description": "retry until resolved or abandoned"},
        treatment={"label": "two-method cap", "description": "stop and escalate after two"},
        corpus={
            "id": "escalation-corpus",
            "description": "Recorded blocker episodes from agent sessions",
            "cases": ["episode-1", "episode-2"],
        },
        scorer={"name": "wasted-attempt-count", "version": "1", "deterministic": True},
        primary_metric={
            "name": "resolution_rate",
            "direction": "maximize",
            "description": "Share of blockers resolved without wasted attempts.",
        },
        pass_threshold=0.80,
        failure_threshold=0.50,
        project=PROJECT,
        knowledge=(claim.id,),
    )
    experiments.freeze(definition.id)
    result = experiments.record_result(
        definition.id,
        primary_metric_value=0.90,
        control_metrics={"resolution_rate": 0.55},
        treatment_metrics={"resolution_rate": 0.90},
        interpretation="Capping recovery raised the resolution rate and cut wasted attempts.",
        started_at="2024-01-31T11:00:00Z",
    )
    assert result.verdict == "pass"

    evidence = EvidenceLedger(layout).record(
        kind="experiment_run",
        summary="Blocker-episode outcomes with and without a two-method recovery cap.",
        project=PROJECT,
        source_type="project_observation",
        source_detail="Recorded agent sessions",
        artifact_bytes=(
            b"uncapped:   11/20 resolved, 34 wasted attempts\n"
            b"two-method: 18/20 resolved,  6 wasted attempts\n"
        ),
        artifact_name="escalation-episodes.txt",
        media_type="text/plain",
        experiment=definition.id,
        knowledge=[claim.id],
    )

    # The skill itself. Scope is 'core' because the policy is project-agnostic,
    # but scope says where it applies -- not that it has been proven.
    skills = SkillStore(layout)
    engine = PromotionEngine(layout)
    policy = skills.create(
        name="Two-method escalation",
        scope="core",
        purpose=(
            "Cap autonomous recovery at two materially different methods, then stop and "
            "ask, so that a blocked agent escalates with evidence instead of thrashing."
        ),
        applies_when=[
            "a blocker, ambiguity or unexplained failure cannot be confidently resolved",
            "a second materially different method has failed",
        ],
        do_not_apply_when=[
            "work is progressing normally, however difficult",
            "the first method has not yet concluded",
            "the failure is transient and the same approach has not been retried once",
        ],
        activation_rules=ACTIVATION_RULES,
        created_by="bundled",
        created_from=[
            "policy:docs/policies/universal-agent-rules.md",
            f"experiment:{definition.id}",
        ],
        origin_project=PROJECT,
        confidence="medium",
    )

    # DEC-0004 as amended by DEC-0015: a bundled policy enters at observed,
    # never validated.
    assert policy.maturity == "observed"
    policy = engine.promote(
        policy.id, "candidate", reason="Applicability and exclusions stated.", actor=ACTOR
    )
    assert policy.maturity == "candidate"

    skills.update(
        policy.id,
        procedure=PROCEDURE,
        success_conditions=[
            "The agent stopped after at most two materially different methods.",
            "The escalation report contains all eight required sections.",
        ],
        failure_modes=[
            "Three variants of one approach are presented as three distinct methods.",
            "Research is used indefinitely to postpone escalating.",
            "The agent escalates on ordinary difficulty, training the user to ignore it.",
        ],
        verification=[
            "Check the report names both methods and why each failed.",
            "Check the diagnosis separates known from likely from unknown.",
        ],
    )
    skills.attach_evidence(
        policy.id, experiments=[definition.id], knowledge=[claim.id], records=[evidence.id]
    )
    policy = engine.promote(
        policy.id, "experimental", reason="Procedure and experiment evidence recorded.", actor=ACTOR
    )

    # Evaluate the activation boundary, both directions.
    write_evaluation_suite(
        layout,
        policy.id,
        corpus_id="escalation-activation",
        pass_threshold=1.0,
        max_false_activation_rate=0.0,
        positive=POSITIVE_CASES,
        negative=NEGATIVE_CASES,
        description="Does the rule fire on real blockers and stay quiet on ordinary work?",
    )
    report = evaluate_skill(layout, policy.id, project=PROJECT)

    assert report.verdict == "pass", f"failure reasons: {report.failure_reasons}"
    assert report.true_positives == len(POSITIVE_CASES)
    assert report.true_negatives == len(NEGATIVE_CASES)
    assert report.false_activations == 0, "the rule must not fire on ordinary work"
    assert report.missed_activations == 0, "the rule must fire on a genuine blocker"

    policy = engine.promote(
        policy.id,
        "validated",
        reason="Activation boundary evaluated in both directions.",
        actor=ACTOR,
    )
    assert policy.maturity == "validated"

    # Reload and verify provenance, as with the synthetic subject.
    reloaded_layout = Layout(root=layout.root)
    provenance = verify_provenance(reloaded_layout, policy.id)
    assert provenance.findings == [], "\n".join(provenance.findings)
    assert provenance.maturity_path == ["observed", "candidate", "experimental", "validated"]

    reloaded = SkillStore(reloaded_layout).get(policy.id)
    assert reloaded.scope == "core"
    assert reloaded.applies_to(["blocked", "second-method-failed"])
    assert not reloaded.applies_to(["routine-progress"])


def test_the_policy_does_not_fire_on_ordinary_difficulty(tmp_path: Path, frozen_now: str) -> None:
    """The failure mode that would make the policy useless.

    A rule that escalates whenever work is hard gets ignored, and an ignored
    safety rule is worse than none. This is asserted separately from the
    lifecycle test because it is a property of the policy, not of the mechanism.
    """
    layout = initialize(tmp_path / "core", project_name=PROJECT)
    skills = SkillStore(layout)
    policy = skills.create(
        name="Two-method escalation",
        scope="core",
        purpose="Cap autonomous recovery at two methods.",
        applies_when=["a blocker that cannot be confidently resolved"],
        do_not_apply_when=["work is progressing normally, however difficult"],
        activation_rules=ACTIVATION_RULES,
        created_by="bundled",
        created_from=["policy:docs/policies/universal-agent-rules.md"],
        origin_project=PROJECT,
    )

    for signals in (
        ["routine-progress"],
        ["routine-progress", "difficult-task"],
        ["first-attempt-underway"],
        ["difficult-task"],
        [],
    ):
        assert not policy.applies_to(signals), f"should not escalate on {signals}"

    # Exclusions beat inclusions: still investigating, even though blocked.
    assert not policy.applies_to(["blocked", "first-attempt-underway"])
    assert policy.applies_to(["blocked"])
