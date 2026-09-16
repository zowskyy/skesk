"""Promotion gates: what must be refused, and whether the refusal explains itself.

The gates are the point of the whole system. A gate that can be talked past is
worse than no gate, because it produces skills that look validated. Every test
here asserts a refusal, and most assert the refusal *names the missing thing* —
a diagnostic that says only "denied" sends the caller guessing.
"""

from __future__ import annotations

from typing import Any

import pytest
from skillkernel.core.errors import GateError, TransitionError
from skillkernel.core.paths import Layout
from skillkernel.core.yamlio import load_yaml_file, write_yaml_file
from skillkernel.evaluation.runner import evaluate_skill
from skillkernel.evaluation.suite import write_evaluation_suite
from skillkernel.experiments.store import ExperimentStore
from skillkernel.knowledge.store import KnowledgeStore
from skillkernel.promotion.engine import PromotionEngine
from skillkernel.skills.store import SkillStore

PROJECT = "fixture-project"
ACTOR = "test"

ACTIVATION = {"require_any": ["trigger"], "require_all": [], "exclude_any": ["forbidden"]}
POSITIVE = [{"case_id": "p1", "signals": ["trigger"]}]
NEGATIVE = [{"case_id": "n1", "signals": ["forbidden"]}]


@pytest.fixture
def engine(layout: Layout) -> PromotionEngine:
    return PromotionEngine(layout)


def make_experiment(layout: Layout, *, frozen: bool = True, with_result: bool = True) -> str:
    store = ExperimentStore(layout)
    definition = store.add(
        title="t",
        hypothesis="h",
        independent_variable="v",
        control={"label": "c", "description": "c"},
        treatment={"label": "t", "description": "t"},
        corpus={"id": "corpus", "description": "d", "cases": ["c1"]},
        scorer={"name": "s", "version": "1", "deterministic": True},
        primary_metric={"name": "m", "direction": "maximize", "description": None},
        pass_threshold=0.9,
        failure_threshold=0.5,
        project=PROJECT,
    )
    if frozen:
        store.freeze(definition.id)
    if frozen and with_result:
        store.record_result(
            definition.id,
            primary_metric_value=1.0,
            control_metrics={"m": 0.5},
            treatment_metrics={"m": 1.0},
            interpretation="worked",
            started_at="2024-01-31T11:00:00Z",
        )
    return definition.id


def make_skill(skills: SkillStore, **overrides: Any) -> Any:
    kwargs: dict[str, Any] = {
        "name": "Sample skill",
        "scope": "project",
        "purpose": "A purpose.",
        "applies_when": ["when triggered"],
        "do_not_apply_when": ["when forbidden"],
        "activation_rules": ACTIVATION,
        "created_from": ["manual:test"],
        "origin_project": PROJECT,
    }
    kwargs.update(overrides)
    return skills.create(**kwargs)


def advance_to_experimental(
    skills: SkillStore, engine: PromotionEngine, layout: Layout, skill_id: str
) -> str:
    experiment_id = make_experiment(layout)
    engine.promote(skill_id, "candidate", reason="ok", actor=ACTOR)
    skills.update(
        skill_id,
        procedure=["do the thing"],
        success_conditions=["it worked"],
        failure_modes=["it did not"],
        verification=["check it"],
    )
    skills.attach_evidence(skill_id, experiments=[experiment_id])
    engine.promote(skill_id, "experimental", reason="ok", actor=ACTOR)
    return experiment_id


# --- candidate gate --------------------------------------------------------


@pytest.mark.parametrize(
    ("omitted", "expected_phrase"),
    [
        ("purpose", "no purpose"),
        ("applies_when", "no applies_when"),
        ("do_not_apply_when", "no do_not_apply_when"),
        ("created_from", "no provenance"),
    ],
)
def test_candidate_gate_names_the_missing_field(
    skills: SkillStore, engine: PromotionEngine, omitted: str, expected_phrase: str
) -> None:
    overrides: dict[str, Any] = {omitted: None if omitted == "purpose" else []}
    skill = make_skill(skills, **overrides)
    with pytest.raises(GateError) as excinfo:
        engine.promote(skill.id, "candidate", reason="try", actor=ACTOR)
    assert expected_phrase in str(excinfo.value)


def test_missing_exclusion_explains_why_it_matters(
    skills: SkillStore, engine: PromotionEngine
) -> None:
    """The diagnostic should teach, not just deny."""
    skill = make_skill(skills, do_not_apply_when=[])
    with pytest.raises(GateError, match="will activate where it should not"):
        engine.promote(skill.id, "candidate", reason="try", actor=ACTOR)


# --- experimental gate -----------------------------------------------------


def test_experimental_requires_a_procedure(skills: SkillStore, engine: PromotionEngine) -> None:
    skill = make_skill(skills)
    engine.promote(skill.id, "candidate", reason="ok", actor=ACTOR)
    with pytest.raises(GateError, match="no procedure"):
        engine.promote(skill.id, "experimental", reason="try", actor=ACTOR)


def test_experimental_requires_experiment_evidence(
    skills: SkillStore, engine: PromotionEngine
) -> None:
    skill = make_skill(skills)
    engine.promote(skill.id, "candidate", reason="ok", actor=ACTOR)
    skills.update(skill.id, procedure=["do it"])
    with pytest.raises(GateError, match="cites no experiment evidence"):
        engine.promote(skill.id, "experimental", reason="try", actor=ACTOR)


def test_an_unfrozen_experiment_does_not_count(
    layout: Layout, skills: SkillStore, engine: PromotionEngine
) -> None:
    experiment_id = make_experiment(layout, frozen=False)
    skill = make_skill(skills)
    engine.promote(skill.id, "candidate", reason="ok", actor=ACTOR)
    skills.update(skill.id, procedure=["do it"])
    skills.attach_evidence(skill.id, experiments=[experiment_id])
    with pytest.raises(GateError, match="is not frozen"):
        engine.promote(skill.id, "experimental", reason="try", actor=ACTOR)


def test_an_experiment_with_no_result_does_not_count(
    layout: Layout, skills: SkillStore, engine: PromotionEngine
) -> None:
    experiment_id = make_experiment(layout, with_result=False)
    skill = make_skill(skills)
    engine.promote(skill.id, "candidate", reason="ok", actor=ACTOR)
    skills.update(skill.id, procedure=["do it"])
    skills.attach_evidence(skill.id, experiments=[experiment_id])
    with pytest.raises(GateError, match="no recorded result"):
        engine.promote(skill.id, "experimental", reason="try", actor=ACTOR)


def test_a_dangling_experiment_reference_is_caught(
    skills: SkillStore, engine: PromotionEngine
) -> None:
    skill = make_skill(skills)
    engine.promote(skill.id, "candidate", reason="ok", actor=ACTOR)
    skills.update(skill.id, procedure=["do it"])
    skills.attach_evidence(skill.id, experiments=["EXP-9999"])
    with pytest.raises(GateError, match="which does not exist"):
        engine.promote(skill.id, "experimental", reason="try", actor=ACTOR)


# --- validated gate --------------------------------------------------------


def test_validated_requires_an_evaluation(
    layout: Layout, skills: SkillStore, engine: PromotionEngine
) -> None:
    skill = make_skill(skills)
    advance_to_experimental(skills, engine, layout, skill.id)
    with pytest.raises(GateError, match="no passing evaluation"):
        engine.promote(skill.id, "validated", reason="try", actor=ACTOR)


def test_a_failing_evaluation_does_not_unlock_validated(
    layout: Layout, skills: SkillStore, engine: PromotionEngine
) -> None:
    """A skill that fires on a negative case must not become validated."""
    skill = make_skill(
        skills,
        activation_rules={
            "require_any": ["trigger", "forbidden"],  # deliberately over-broad
            "require_all": [],
            "exclude_any": [],
        },
    )
    advance_to_experimental(skills, engine, layout, skill.id)
    write_evaluation_suite(
        layout,
        skill.id,
        corpus_id="corpus-a",
        pass_threshold=1.0,
        max_false_activation_rate=0.0,
        positive=POSITIVE,
        negative=NEGATIVE,
    )
    report = evaluate_skill(layout, skill.id, project=PROJECT)

    assert report.verdict == "fail"
    assert report.false_activations == 1
    assert any("false activation" in reason for reason in report.failure_reasons)

    with pytest.raises(GateError, match="no passing evaluation"):
        engine.promote(skill.id, "validated", reason="try", actor=ACTOR)


def test_a_stale_evaluation_does_not_count_and_says_so(
    layout: Layout, skills: SkillStore, engine: PromotionEngine
) -> None:
    """Editing behaviour after evaluating invalidates the evaluation."""
    skill = make_skill(skills)
    advance_to_experimental(skills, engine, layout, skill.id)
    write_evaluation_suite(
        layout,
        skill.id,
        corpus_id="corpus-a",
        pass_threshold=1.0,
        max_false_activation_rate=0.0,
        positive=POSITIVE,
        negative=NEGATIVE,
    )
    assert evaluate_skill(layout, skill.id, project=PROJECT).verdict == "pass"

    # Rewrite the procedure: the evaluation no longer describes this skill.
    skills.update(skill.id, procedure=["an entirely different procedure"])

    with pytest.raises(GateError) as excinfo:
        engine.promote(skill.id, "validated", reason="try", actor=ACTOR)
    message = str(excinfo.value)
    assert "different version of this skill" in message
    assert "Re-evaluate" in message


def test_re_evaluating_after_an_edit_restores_the_path(
    layout: Layout, skills: SkillStore, engine: PromotionEngine
) -> None:
    """The stale-evaluation guard must be recoverable, not a dead end."""
    skill = make_skill(skills)
    advance_to_experimental(skills, engine, layout, skill.id)
    write_evaluation_suite(
        layout,
        skill.id,
        corpus_id="corpus-a",
        pass_threshold=1.0,
        max_false_activation_rate=0.0,
        positive=POSITIVE,
        negative=NEGATIVE,
    )
    evaluate_skill(layout, skill.id, project=PROJECT)
    skills.update(skill.id, procedure=["a revised procedure"])
    evaluate_skill(layout, skill.id, project=PROJECT)

    promoted = engine.promote(skill.id, "validated", reason="re-evaluated", actor=ACTOR)
    assert promoted.maturity == "validated"


def test_refuted_knowledge_blocks_promotion_at_any_maturity(
    layout: Layout, skills: SkillStore, engine: PromotionEngine
) -> None:
    knowledge = KnowledgeStore(layout)
    claim = knowledge.add(statement="A claim.", scope="universal", source_type="research")
    skill = make_skill(skills)
    skills.attach_evidence(skill.id, knowledge=[claim.id])
    knowledge.refute(claim.id, reason="Measured the opposite.")

    with pytest.raises(GateError, match="refuted knowledge"):
        engine.promote(skill.id, "candidate", reason="try", actor=ACTOR)


# --- refusals must not half-apply ------------------------------------------


def test_a_refused_promotion_changes_nothing_on_disk(
    layout: Layout, skills: SkillStore, engine: PromotionEngine
) -> None:
    skill = make_skill(skills)
    engine.promote(skill.id, "candidate", reason="ok", actor=ACTOR)
    before = SkillStore(layout).get(skill.id).content_hash()
    history_before = skills.history(skill.id)

    with pytest.raises(GateError):
        engine.promote(skill.id, "experimental", reason="try", actor=ACTOR)

    reloaded = SkillStore(Layout(root=layout.root))
    assert reloaded.get(skill.id).content_hash() == before
    assert reloaded.get(skill.id).maturity == "candidate"
    assert reloaded.history(skill.id) == history_before


def test_dry_run_reports_without_changing_anything(
    layout: Layout, skills: SkillStore, engine: PromotionEngine
) -> None:
    skill = make_skill(skills)
    engine.promote(skill.id, "candidate", reason="ok", actor=ACTOR)
    report = engine.dry_run(skill.id, "experimental")

    assert not report.passed
    assert any("procedure" in reason for reason in report.reasons)
    assert SkillStore(layout).get(skill.id).maturity == "candidate"


# --- the state machine still governs ---------------------------------------


def test_the_engine_still_rejects_an_illegal_transition(
    skills: SkillStore, engine: PromotionEngine
) -> None:
    """Shape is checked before evidence: skipping a rung is never gate-able."""
    skill = make_skill(skills)
    with pytest.raises(TransitionError):
        engine.promote(skill.id, "validated", reason="skip ahead", actor=ACTOR)


def test_a_promotion_must_state_a_reason(skills: SkillStore, engine: PromotionEngine) -> None:
    skill = make_skill(skills)
    with pytest.raises(GateError, match="must state a reason"):
        engine.promote(skill.id, "candidate", reason="   ", actor=ACTOR)


# --- trusted requires distinct corpora -------------------------------------


def test_trusted_requires_evaluations_across_distinct_corpora(
    layout: Layout, skills: SkillStore, engine: PromotionEngine
) -> None:
    skill = make_skill(skills)
    advance_to_experimental(skills, engine, layout, skill.id)
    write_evaluation_suite(
        layout,
        skill.id,
        corpus_id="corpus-a",
        pass_threshold=1.0,
        max_false_activation_rate=0.0,
        positive=POSITIVE,
        negative=NEGATIVE,
    )
    evaluate_skill(layout, skill.id, project=PROJECT)
    engine.promote(skill.id, "validated", reason="evaluated", actor=ACTOR)

    # Re-running the SAME corpus is repetition, not independent confirmation.
    evaluate_skill(layout, skill.id, project=PROJECT)
    with pytest.raises(GateError, match="distinct corpus"):
        engine.promote(skill.id, "trusted", reason="try", actor=ACTOR)


def test_trusted_is_reached_with_a_second_distinct_corpus(
    layout: Layout, skills: SkillStore, engine: PromotionEngine
) -> None:
    skill = make_skill(skills)
    advance_to_experimental(skills, engine, layout, skill.id)
    write_evaluation_suite(
        layout,
        skill.id,
        corpus_id="corpus-a",
        pass_threshold=1.0,
        max_false_activation_rate=0.0,
        positive=POSITIVE,
        negative=NEGATIVE,
    )
    evaluate_skill(layout, skill.id, project=PROJECT)
    engine.promote(skill.id, "validated", reason="evaluated", actor=ACTOR)

    write_evaluation_suite(
        layout,
        skill.id,
        corpus_id="corpus-b",
        pass_threshold=1.0,
        max_false_activation_rate=0.0,
        positive=[{"case_id": "p2", "signals": ["trigger", "other"]}],
        negative=[{"case_id": "n2", "signals": ["forbidden", "other"]}],
    )
    evaluate_skill(layout, skill.id, project=PROJECT)

    promoted = engine.promote(skill.id, "trusted", reason="two corpora", actor=ACTOR)
    assert promoted.maturity == "trusted"


# --- history cannot be forged ----------------------------------------------


def test_a_hand_edited_maturity_is_detected_by_the_history_chain(
    layout: Layout, skills: SkillStore, engine: PromotionEngine
) -> None:
    """Editing skill.yaml directly must not produce a credible validated skill."""
    from skillkernel.validation.provenance import verify_provenance

    skill = make_skill(skills)
    engine.promote(skill.id, "candidate", reason="ok", actor=ACTOR)

    path = skills.registry.path_of(skill.id)
    document = load_yaml_file(path)
    document["classification"]["maturity"] = "trusted"
    write_yaml_file(path, document)

    report = verify_provenance(Layout(root=layout.root), skill.id)
    assert any("without a recorded transition" in finding for finding in report.findings)
