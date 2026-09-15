"""Bootstrap, skill store, evaluation scoring and provenance verification."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from skillkernel.core.errors import UnsafeOperationError, ValidationError
from skillkernel.core.paths import Layout
from skillkernel.core.yamlio import load_yaml_file, write_yaml_file
from skillkernel.evaluation.runner import derive_verdict, evaluate_skill
from skillkernel.evaluation.scorer import score_activation
from skillkernel.evaluation.suite import (
    EvaluationCase,
    load_evaluation_suite,
    write_evaluation_suite,
)
from skillkernel.evidence.ledger import EvidenceLedger
from skillkernel.project.bootstrap import initialize, initialize_with_report, is_initialized
from skillkernel.skills.store import SkillStore
from skillkernel.validation.provenance import verify_provenance

PROJECT = "fixture-project"
ACTIVATION = {"require_any": ["trigger"], "require_all": [], "exclude_any": ["forbidden"]}


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


# --- bootstrap -------------------------------------------------------------


def test_initialize_creates_a_usable_workspace(tmp_path: Path, frozen_now: str) -> None:
    layout = initialize(tmp_path / "ws", project_name="demo")
    assert is_initialized(layout.root)
    assert layout.config_file.is_file()
    assert layout.profile_file.is_file()
    assert layout.profile_doc.is_file()
    for directory in layout.managed_directories():
        assert directory.is_dir()


def test_initialize_creates_every_registry(tmp_path: Path, frozen_now: str) -> None:
    report = initialize_with_report(tmp_path / "ws", project_name="demo")
    assert set(report.created_registries) == {
        "knowledge",
        "experiments",
        "evidence",
        "observations",
        "skills",
    }


def test_initialize_refuses_to_overwrite_an_existing_workspace(
    tmp_path: Path, frozen_now: str
) -> None:
    root = tmp_path / "ws"
    initialize(root, project_name="demo")
    with pytest.raises(UnsafeOperationError, match="refusing to overwrite"):
        initialize(root, project_name="demo-again")


def test_initialize_does_not_disturb_unrelated_files(tmp_path: Path, frozen_now: str) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    keeper = root / "README.md"
    keeper.write_text("pre-existing content")
    initialize(root, project_name="demo")
    assert keeper.read_text() == "pre-existing content"


def test_the_initial_profile_invents_nothing(tmp_path: Path, frozen_now: str) -> None:
    from skillkernel.project.profile import load_profile

    layout = initialize(tmp_path / "ws", project_name="demo")
    profile = load_profile(layout)
    assert profile.name == "demo"
    assert profile.domains == ()
    assert profile.objectives == ()


def test_initialize_refuses_a_path_that_is_a_file(tmp_path: Path, frozen_now: str) -> None:
    target = tmp_path / "afile"
    target.write_text("x")
    with pytest.raises(UnsafeOperationError, match="not a directory"):
        initialize(target, project_name="demo")


# --- skill store -----------------------------------------------------------


def test_a_new_skill_starts_observed_with_an_opening_history_entry(
    skills: SkillStore,
) -> None:
    skill = make_skill(skills)
    assert skill.maturity == "observed"
    assert skills.maturity_path(skill.id) == ["observed"]


def test_a_skill_is_stored_under_its_scope_directory(layout: Layout, skills: SkillStore) -> None:
    skill = make_skill(skills)
    expected = layout.skills_dir / "project" / "sample-skill" / "skill.yaml"
    assert skills.registry.path_of(skill.id) == expected


def test_scope_separation_is_enforced_by_the_store(skills: SkillStore) -> None:
    """A project skill must not surface as a core skill."""
    project_skill = make_skill(skills, name="Project one", scope="project")
    core_skill = make_skill(skills, name="Core one", scope="core")

    assert [record.id for record in skills.in_scope("project")] == [project_skill.id]
    assert [record.id for record in skills.in_scope("core")] == [core_skill.id]


def test_a_duplicate_slug_within_a_scope_is_refused(skills: SkillStore) -> None:
    make_skill(skills, name="Same name")
    with pytest.raises(ValidationError, match="already exists"):
        make_skill(skills, name="Same name")


def test_the_same_slug_in_a_different_scope_is_allowed(skills: SkillStore) -> None:
    make_skill(skills, name="Shared", scope="project")
    assert make_skill(skills, name="Shared", scope="core").scope == "core"


def test_maturity_cannot_be_edited_through_the_store(skills: SkillStore) -> None:
    """The one field the store must never let a caller touch."""
    skill = make_skill(skills)
    with pytest.raises(ValidationError, match="promotion engine"):
        skills.update(skill.id, classification={"scope": "project", "maturity": "trusted"})


@pytest.mark.parametrize("field", ["id", "evidence", "provenance", "project_scope"])
def test_kernel_owned_fields_cannot_be_edited(skills: SkillStore, field: str) -> None:
    skill = make_skill(skills)
    with pytest.raises(ValidationError, match="may not be edited directly"):
        skills.update(skill.id, **{field: "tampered"})


def test_update_requires_at_least_one_field(skills: SkillStore) -> None:
    skill = make_skill(skills)
    with pytest.raises(ValidationError, match="at least one field"):
        skills.update(skill.id)


def test_attaching_evidence_merges_rather_than_replaces(skills: SkillStore) -> None:
    skill = make_skill(skills)
    skills.attach_evidence(skill.id, experiments=["EXP-0001"])
    updated = skills.attach_evidence(skill.id, experiments=["EXP-0002"], knowledge=["K-0001"])
    assert updated.experiment_ids == ("EXP-0001", "EXP-0002")
    assert updated.knowledge_ids == ("K-0001",)


def test_editing_content_does_not_change_the_maturity(skills: SkillStore) -> None:
    skill = make_skill(skills)
    assert skills.update(skill.id, procedure=["step"]).maturity == "observed"


# --- evaluation suite ------------------------------------------------------


def test_a_suite_requires_negative_cases(layout: Layout, skills: SkillStore) -> None:
    """Without a negative case a suite cannot measure over-activation."""
    skill = make_skill(skills)
    with pytest.raises(ValidationError, match="dangerous direction"):
        write_evaluation_suite(
            layout,
            skill.id,
            corpus_id="c",
            pass_threshold=1.0,
            max_false_activation_rate=0.0,
            positive=[{"case_id": "p1", "signals": ["trigger"]}],
            negative=[],
        )


def test_a_suite_requires_positive_cases(layout: Layout, skills: SkillStore) -> None:
    skill = make_skill(skills)
    with pytest.raises(ValidationError, match="at least one positive case"):
        write_evaluation_suite(
            layout,
            skill.id,
            corpus_id="c",
            pass_threshold=1.0,
            max_false_activation_rate=0.0,
            positive=[],
            negative=[{"case_id": "n1", "signals": ["forbidden"]}],
        )


def test_duplicate_case_ids_are_refused(layout: Layout, skills: SkillStore) -> None:
    skill = make_skill(skills)
    with pytest.raises(ValidationError, match="duplicate case_id"):
        write_evaluation_suite(
            layout,
            skill.id,
            corpus_id="c",
            pass_threshold=1.0,
            max_false_activation_rate=0.0,
            positive=[{"case_id": "dup", "signals": ["trigger"]}],
            negative=[{"case_id": "dup", "signals": ["forbidden"]}],
        )


def test_evaluating_without_a_suite_says_what_to_do(layout: Layout, skills: SkillStore) -> None:
    skill = make_skill(skills)
    with pytest.raises(ValidationError, match="write_evaluation_suite"):
        load_evaluation_suite(layout, skill.id)


def test_a_suite_round_trips(layout: Layout, skills: SkillStore) -> None:
    skill = make_skill(skills)
    suite = write_evaluation_suite(
        layout,
        skill.id,
        corpus_id="corpus-a",
        pass_threshold=0.9,
        max_false_activation_rate=0.1,
        positive=[{"case_id": "p1", "signals": ["trigger"]}],
        negative=[{"case_id": "n1", "signals": ["forbidden"]}],
    )
    assert suite.corpus_id == "corpus-a"
    assert len(suite.positive_cases) == 1
    assert len(suite.negative_cases) == 1
    assert load_evaluation_suite(layout, skill.id) == suite


# --- scorer ----------------------------------------------------------------


def case(case_id: str, signals: list[str], expected: str) -> EvaluationCase:
    return EvaluationCase(
        case_id=case_id, expected=expected, signals=tuple(signals), description=None
    )


def test_the_scorer_separates_all_four_outcome_classes(skills: SkillStore) -> None:
    """DEC-0003: four counts, never one aggregate."""
    skill = make_skill(skills)
    breakdown = score_activation(
        skill,
        [
            case("tp", ["trigger"], "applies"),
            case("miss", ["unrelated"], "applies"),
            case("tn", ["forbidden"], "does_not_apply"),
            case("fa", ["trigger"], "does_not_apply"),
        ],
    )
    assert breakdown.true_positives == 1
    assert breakdown.missed_activations == 1
    assert breakdown.true_negatives == 1
    assert breakdown.false_activations == 1
    assert breakdown.accuracy == 0.5


def test_false_and_missed_activation_rates_are_computed_separately(
    skills: SkillStore,
) -> None:
    """A skill can be perfect on one axis and terrible on the other."""
    skill = make_skill(skills)
    breakdown = score_activation(
        skill,
        [
            case("p1", ["trigger"], "applies"),
            case("p2", ["trigger"], "applies"),
            case("n1", ["forbidden"], "does_not_apply"),
            case("n2", ["unrelated"], "does_not_apply"),
        ],
    )
    assert breakdown.false_activation_rate == 0.0
    assert breakdown.missed_activation_rate == 0.0


def test_scoring_is_deterministic(skills: SkillStore) -> None:
    skill = make_skill(skills)
    cases = [case("p1", ["trigger"], "applies"), case("n1", ["forbidden"], "does_not_apply")]
    assert (
        score_activation(skill, cases).to_document() == score_activation(skill, cases).to_document()
    )


def test_case_order_does_not_affect_the_result(skills: SkillStore) -> None:
    skill = make_skill(skills)
    forward = [case("a", ["trigger"], "applies"), case("b", ["forbidden"], "does_not_apply")]
    assert (
        score_activation(skill, forward).to_document()
        == score_activation(skill, list(reversed(forward))).to_document()
    )


# --- verdict derivation ----------------------------------------------------


def test_a_guardrail_breach_fails_even_with_high_accuracy(skills: SkillStore) -> None:
    """Accuracy must not be able to mask over-activation."""
    from skillkernel.evaluation.suite import EvaluationSuite

    skill = make_skill(skills)
    cases = [case(f"p{i}", ["trigger"], "applies") for i in range(9)]
    cases.append(case("fa", ["trigger"], "does_not_apply"))
    breakdown = score_activation(skill, cases)

    suite = EvaluationSuite(
        skill_id=skill.id,
        corpus_id="c",
        pass_threshold=0.85,
        max_false_activation_rate=0.0,
        scorer="activation-boundary",
        scorer_version="1",
        cases=tuple(cases),
    )
    verdict, reasons = derive_verdict(breakdown, suite)

    assert breakdown.accuracy >= 0.85
    assert verdict == "fail"
    assert any("false activation" in reason for reason in reasons)


# --- evaluation evidence ---------------------------------------------------


def test_the_evaluation_report_is_recorded_as_hashed_evidence(
    layout: Layout, skills: SkillStore
) -> None:
    skill = make_skill(skills)
    write_evaluation_suite(
        layout,
        skill.id,
        corpus_id="corpus-a",
        pass_threshold=1.0,
        max_false_activation_rate=0.0,
        positive=[{"case_id": "p1", "signals": ["trigger"]}],
        negative=[{"case_id": "n1", "signals": ["forbidden"]}],
    )
    report = evaluate_skill(layout, skill.id, project=PROJECT)

    assert report.evidence_id is not None
    record = EvidenceLedger(layout).get(report.evidence_id)
    assert record.kind == "evaluation_report"
    assert record.skill_id == skill.id
    assert record.skill_fingerprint == skill.fingerprint()
    assert record.attributes["verdict"] == "pass"
    assert EvidenceLedger(layout).verify() == []


def test_the_report_artifact_is_byte_identical_for_identical_input(
    layout: Layout, skills: SkillStore
) -> None:
    skill = make_skill(skills)
    write_evaluation_suite(
        layout,
        skill.id,
        corpus_id="corpus-a",
        pass_threshold=1.0,
        max_false_activation_rate=0.0,
        positive=[{"case_id": "p1", "signals": ["trigger"]}],
        negative=[{"case_id": "n1", "signals": ["forbidden"]}],
    )
    first = evaluate_skill(layout, skill.id, project=PROJECT, record_evidence=False)
    second = evaluate_skill(layout, skill.id, project=PROJECT, record_evidence=False)
    assert first.render_json() == second.render_json()


# --- provenance ------------------------------------------------------------


def test_provenance_reports_a_missing_skill_rather_than_raising(layout: Layout) -> None:
    report = verify_provenance(layout, "SKILL-9999")
    assert not report.ok
    assert any("not a registered skill" in finding for finding in report.findings)


def test_provenance_flags_a_dangling_experiment_reference(
    layout: Layout, skills: SkillStore
) -> None:
    skill = make_skill(skills)
    skills.attach_evidence(skill.id, experiments=["EXP-9999"])
    report = verify_provenance(layout, skill.id)
    assert any("EXP-9999" in finding for finding in report.findings)


def test_provenance_flags_refuted_knowledge(layout: Layout, skills: SkillStore) -> None:
    from skillkernel.knowledge.store import KnowledgeStore

    knowledge = KnowledgeStore(layout)
    claim = knowledge.add(statement="A claim.", scope="universal", source_type="research")
    skill = make_skill(skills)
    skills.attach_evidence(skill.id, knowledge=[claim.id])
    knowledge.refute(claim.id, reason="Disproved.")

    report = verify_provenance(layout, skill.id)
    assert any("refuted knowledge" in finding for finding in report.findings)


def test_provenance_flags_a_tampered_evaluation_artifact(
    layout: Layout, skills: SkillStore
) -> None:
    skill = make_skill(skills)
    write_evaluation_suite(
        layout,
        skill.id,
        corpus_id="corpus-a",
        pass_threshold=1.0,
        max_false_activation_rate=0.0,
        positive=[{"case_id": "p1", "signals": ["trigger"]}],
        negative=[{"case_id": "n1", "signals": ["forbidden"]}],
    )
    report = evaluate_skill(layout, skill.id, project=PROJECT)
    assert report.evidence_id is not None

    record = EvidenceLedger(layout).get(report.evidence_id)
    assert record.artifact is not None
    (layout.root / str(record.artifact["path"])).write_text("{}")

    provenance = verify_provenance(layout, skill.id)
    assert any("does not match its recorded hash" in finding for finding in provenance.findings)


def test_provenance_flags_a_corrupted_history_chain(layout: Layout, skills: SkillStore) -> None:
    skill = make_skill(skills)
    path = skills.history_path(skill.id)
    document = load_yaml_file(path)
    document["transitions"][0]["new_state"] = "validated"
    write_yaml_file(path, document)

    report = verify_provenance(Layout(root=layout.root), skill.id)
    assert not report.ok
