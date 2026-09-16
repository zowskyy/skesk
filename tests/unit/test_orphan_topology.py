"""Invariant B: orphan discovery enumerates the topology each domain actually writes.

Measured at ``cec6576``: ``Registry.orphan_record_files()`` globbed
``<domain>/<records_subdir>/*.yaml`` non-recursively, so it detected exactly the
shapes the stores never produce and missed exactly the shapes they do.

* skills are written to ``<scope>/<slug>/skill.yaml``; ``skills/records/`` is
  never created, so the glob always returned ``[]``;
* experiment definitions are written to ``definitions/<EXP-ID>/v<N>.yaml``, one
  level below a non-recursive ``definitions/*.yaml``.

Two of five domains were blind. The skills case is the one ``DEC-0015`` and
``ARCHITECTURE.md`` record; the experiments case was documented nowhere.

**What counts as an orphan is deliberately bounded.** For a skill it is an
unindexed *canonical* ``<scope>/<canonical-slug>/`` directory holding at least
one managed artifact (``skill.yaml`` or ``history.yaml``). That catches both a
complete unindexed skill and the residue an interrupted create leaves, while
leaving malformed names, empty directories, loose files and nested debris out.
``doctor`` is not a filesystem linter: it reports recoverable-looking managed
state that no index owns.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from skillkernel.core.paths import Layout
from skillkernel.evidence.ledger import EvidenceLedger
from skillkernel.experiments.store import ExperimentStore
from skillkernel.knowledge.store import KnowledgeStore
from skillkernel.skills.store import SkillStore, skills_registry
from skillkernel.validation.doctor import ERROR, WARNING, run_doctor

ACTIVATION = {"require_any": ["trigger"], "require_all": [], "exclude_any": []}


def make_skill(skills: SkillStore, slug: str = "real-skill", scope: str = "core") -> Any:
    return skills.create(
        name=slug.replace("-", " ").title(),
        scope=scope,
        slug=slug,
        purpose="A purpose.",
        applies_when=["when triggered"],
        do_not_apply_when=["when forbidden"],
        activation_rules=ACTIVATION,
    )


def make_experiment(layout: Layout) -> Any:
    return ExperimentStore(layout).add(
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
        project="fixture-project",
    )


def orphans(registry: Any, layout: Layout) -> list[str]:
    return sorted(layout.relative(path) for path in registry.orphan_states())


def warnings_for(layout: Layout, code: str) -> list[str]:
    report = run_doctor(layout)
    assert report.is_complete, report.internal_errors
    return sorted(
        finding.location
        for finding in report.findings
        if finding.severity == WARNING and finding.code == code
    )


def plant_skill_dir(layout: Layout, relative: str, *, artifacts: tuple[str, ...]) -> Path:
    directory = layout.skills_dir / relative
    directory.mkdir(parents=True, exist_ok=True)
    for name in artifacts:
        (directory / name).write_text("stale: true\n", encoding="utf-8")
    return directory


# --- skills: what IS a record-shaped orphan --------------------------------

DETECTED = [
    pytest.param(("skill.yaml",), id="record-only"),
    pytest.param(("history.yaml",), id="interrupted-history-only"),
    pytest.param(("skill.yaml", "history.yaml"), id="complete-unindexed-skill"),
]


@pytest.mark.parametrize("artifacts", DETECTED)
@pytest.mark.parametrize("scope", ["core", "project", "discovered"])
def test_an_unindexed_canonical_skill_directory_is_an_orphan(
    layout: Layout, scope: str, artifacts: tuple[str, ...]
) -> None:
    plant_skill_dir(layout, f"{scope}/abandoned-skill", artifacts=artifacts)
    assert orphans(skills_registry(layout), layout) == [f"skills/{scope}/abandoned-skill"]


@pytest.mark.parametrize("artifacts", DETECTED)
def test_doctor_reports_it_as_a_warning(layout: Layout, artifacts: tuple[str, ...]) -> None:
    """Severity is unchanged policy: an orphan is recoverable and must not fail the gate."""
    plant_skill_dir(layout, "core/abandoned-skill", artifacts=artifacts)
    report = run_doctor(layout)
    assert report.is_complete
    assert not report.has_errors
    assert warnings_for(layout, "registry:skills") == ["skills/core/abandoned-skill"]


def test_one_directory_yields_exactly_one_finding(layout: Layout) -> None:
    """Two managed artifacts are one orphaned skill, not two orphans."""
    plant_skill_dir(layout, "core/abandoned-skill", artifacts=("skill.yaml", "history.yaml"))
    assert len(skills_registry(layout).orphan_states()) == 1


def test_an_indexed_skill_is_not_an_orphan(layout: Layout, skills: SkillStore) -> None:
    make_skill(skills)
    assert orphans(skills.registry, layout) == []
    assert warnings_for(layout, "registry:skills") == []


def test_an_indexed_and_an_orphaned_skill_coexist(layout: Layout, skills: SkillStore) -> None:
    make_skill(skills, slug="owned")
    plant_skill_dir(layout, "core/abandoned-skill", artifacts=("skill.yaml",))
    assert orphans(skills.registry, layout) == ["skills/core/abandoned-skill"]


# --- skills: what is NOT an orphan -----------------------------------------


def test_debris_beneath_skill_topology_is_not_a_record(layout: Layout, skills: SkillStore) -> None:
    """The bounded definition, stated as exclusions. doctor is not a filesystem linter."""
    record = make_skill(skills)
    plant_skill_dir(layout, "core/Not_A_Slug", artifacts=("skill.yaml",))
    plant_skill_dir(layout, "core/-leading-hyphen", artifacts=("skill.yaml",))
    plant_skill_dir(layout, "core/under_score", artifacts=("skill.yaml",))
    (layout.skills_dir / "core" / "empty-dir").mkdir()
    (layout.skills_dir / "core" / "junk.txt").write_text("junk", encoding="utf-8")
    (layout.skills_dir / "core" / "loose.yaml").write_text("loose: 1", encoding="utf-8")
    plant_skill_dir(layout, "core/no-managed-artifact", artifacts=("notes.md",))
    nested = layout.skills_dir / "core" / record.slug / "nested" / "deep"
    nested.mkdir(parents=True)
    (nested / "surprise.yaml").write_text("surprise: 1", encoding="utf-8")

    assert orphans(skills.registry, layout) == []
    assert warnings_for(layout, "registry:skills") == []


def test_the_deprecated_directory_is_not_a_skill_scope(layout: Layout, skills: SkillStore) -> None:
    """``deprecated`` is a maturity, not a scope, so nothing beneath it is a record."""
    (layout.deprecated_skills_dir / "stray").mkdir(parents=True, exist_ok=True)
    (layout.deprecated_skills_dir / "stray" / "skill.yaml").write_text("x: 1", encoding="utf-8")
    assert orphans(skills.registry, layout) == []


def test_a_nested_directory_inside_a_skill_is_not_a_second_skill(
    layout: Layout, skills: SkillStore
) -> None:
    record = make_skill(skills)
    inner = layout.skills_dir / "core" / record.slug / "looks-like-a-skill"
    inner.mkdir()
    (inner / "skill.yaml").write_text("x: 1", encoding="utf-8")
    assert orphans(skills.registry, layout) == []


# --- experiments ------------------------------------------------------------


def test_a_nested_experiment_definition_orphan_is_detected(layout: Layout) -> None:
    store = ExperimentStore(layout)
    make_experiment(layout)
    orphan = layout.experiments_dir / "definitions" / "EXP-0099" / "v1.yaml"
    orphan.parent.mkdir(parents=True)
    orphan.write_text("id: EXP-0099\n", encoding="utf-8")
    assert orphans(store.registry, layout) == ["experiments/definitions/EXP-0099"]
    assert warnings_for(layout, "registry:experiments") == ["experiments/definitions/EXP-0099"]


def test_an_orphaned_definition_is_one_finding_however_many_versions(layout: Layout) -> None:
    """The unit is the experiment, not the file: three versions are one orphan."""
    store = ExperimentStore(layout)
    orphan = layout.experiments_dir / "definitions" / "EXP-0099"
    orphan.mkdir(parents=True)
    for version in (1, 2, 3):
        (orphan / f"v{version}.yaml").write_text("id: EXP-0099\n", encoding="utf-8")
    assert orphans(store.registry, layout) == ["experiments/definitions/EXP-0099"]


def test_a_revised_definitions_earlier_version_is_not_an_orphan(layout: Layout) -> None:
    """The false positive that fixes the unit at the directory.

    ``revise`` writes ``v2.yaml`` and repoints the index at it, so ``v1.yaml``
    stops being referenced while remaining entirely legitimate -- results were
    recorded against it and it is deliberately never rewritten. Enumerating
    definition *files* would report it as an orphan on every revised experiment.
    """
    store = ExperimentStore(layout)
    definition = make_experiment(layout)
    store.freeze(definition.id)
    store.revise(definition.id, changes={"pass_threshold": 0.95}, reason="raise the bar")

    assert store.definition_path(definition.id, 1).is_file()
    assert store.versions(definition.id) == [1, 2]
    assert orphans(store.registry, layout) == []
    assert warnings_for(layout, "registry:experiments") == []


def test_the_legacy_flat_definition_shape_is_still_detected(layout: Layout) -> None:
    """Existing detection is preserved, not traded away for the real topology."""
    store = ExperimentStore(layout)
    flat = layout.experiments_dir / "definitions" / "EXP-0098.yaml"
    flat.write_text("id: EXP-0098\n", encoding="utf-8")
    assert orphans(store.registry, layout) == ["experiments/definitions/EXP-0098.yaml"]


def test_both_shapes_are_reported_without_duplication(layout: Layout) -> None:
    store = ExperimentStore(layout)
    (layout.experiments_dir / "definitions" / "EXP-0098.yaml").write_text("a: 1", encoding="utf-8")
    nested = layout.experiments_dir / "definitions" / "EXP-0099"
    nested.mkdir(parents=True)
    (nested / "v1.yaml").write_text("b: 1", encoding="utf-8")
    found = store.registry.orphan_states()
    assert len(found) == len(set(found)), "an overlapping enumeration produced duplicates"
    assert orphans(store.registry, layout) == [
        "experiments/definitions/EXP-0098.yaml",
        "experiments/definitions/EXP-0099",
    ]


def test_a_registered_experiment_is_not_an_orphan(layout: Layout) -> None:
    store = ExperimentStore(layout)
    make_experiment(layout)
    assert orphans(store.registry, layout) == []
    assert warnings_for(layout, "registry:experiments") == []


def test_experiment_results_are_not_definitions(layout: Layout) -> None:
    """``results/`` is a different tree and must not leak into definition orphans."""
    store = ExperimentStore(layout)
    definition = make_experiment(layout)
    store.freeze(definition.id)
    store.record_result(
        definition.id,
        primary_metric_value=1.0,
        control_metrics={"m": 0.5},
        treatment_metrics={"m": 1.0},
        interpretation="worked",
        started_at="2024-01-31T11:00:00Z",
    )
    assert orphans(store.registry, layout) == []


# --- the flat domains must be untouched -------------------------------------


def test_flat_domains_keep_their_existing_behaviour(layout: Layout) -> None:
    knowledge = KnowledgeStore(layout)
    knowledge.add(
        statement="A testable claim about the world.", scope="project", source_type="research"
    )
    assert orphans(knowledge.registry, layout) == []

    stray = layout.knowledge_dir / "records" / "K-9999.yaml"
    stray.write_text("id: K-9999\n", encoding="utf-8")
    assert orphans(knowledge.registry, layout) == ["knowledge/records/K-9999.yaml"]
    assert warnings_for(layout, "registry:knowledge") == ["knowledge/records/K-9999.yaml"]


def test_the_evidence_domain_is_unchanged(layout: Layout) -> None:
    ledger = EvidenceLedger(layout)
    stray = layout.evidence_dir / "records" / "EV-9999.yaml"
    stray.write_text("id: EV-9999\n", encoding="utf-8")
    assert orphans(ledger.registry, layout) == ["evidence/records/EV-9999.yaml"]


def test_a_clean_workspace_reports_no_orphan_anywhere(layout: Layout, skills: SkillStore) -> None:
    """False positives are the failure mode that would make this unusable."""
    make_skill(skills)
    make_experiment(layout)
    KnowledgeStore(layout).add(
        statement="A testable claim about the world.", scope="project", source_type="research"
    )
    report = run_doctor(layout)
    assert report.is_complete
    assert [finding for finding in report.findings if finding.severity in (ERROR, WARNING)] == []


# --- preserved behaviour from earlier slices --------------------------------


def test_an_indexed_skill_with_no_directory_is_still_an_error(
    layout: Layout, skills: SkillStore
) -> None:
    import shutil

    record = make_skill(skills)
    shutil.rmtree(layout.skills_dir / "core" / record.slug)
    report = run_doctor(layout)
    assert report.has_errors
    assert report.is_complete


def test_orphan_enumeration_tolerates_a_missing_tree(layout: Layout, tmp_path: Path) -> None:
    from skillkernel.core.ids import KNOWLEDGE
    from skillkernel.registry import Registry

    empty = Registry(layout, kind="knowledge", domain_dir=tmp_path / "nowhere", id_prefix=KNOWLEDGE)
    assert empty.orphan_states() == []
