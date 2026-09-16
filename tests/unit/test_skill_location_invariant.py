"""The skill location invariant, attacked from every angle the schema permits.

The invariant: a persisted skill's declared identity (``classification.scope``
and ``slug``) must agree with where it is registered and stored. Both fields are
immutable through ordinary persistence; relocation is a future gated operation.

These tests exist because the alternative was demonstrated to destroy data. A
mutated scope or slug left the record registered at its original path, which
freed the ``(scope, slug)`` pair, which let a second skill be created in the
same directory and overwrite the first one's record and history.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
from skillkernel.core.errors import LocationInvariantError, ValidationError
from skillkernel.core.paths import SKILL_FILENAME, SKILL_SCOPES, Layout
from skillkernel.core.yamlio import load_yaml_file, write_yaml_file
from skillkernel.experiments.store import ExperimentStore
from skillkernel.promotion.engine import PromotionEngine
from skillkernel.skills.model import SkillRecord
from skillkernel.skills.store import SkillStore
from skillkernel.validation.doctor import run_doctor

ACTIVATION = {"require_any": ["trigger"], "require_all": [], "exclude_any": ["stop"]}
ACTOR = "test"


def fingerprint(root: Path) -> dict[str, str]:
    """Hash every file, refusing a vacuous empty/missing tree (VS2 lesson)."""
    assert root.is_dir(), f"{root} is not a directory"
    digests = {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
    assert digests, f"{root} contains no files; comparison would be vacuous"
    return digests


def make(store: SkillStore, **overrides: Any) -> SkillRecord:
    kwargs: dict[str, Any] = {
        "name": "Target Skill",
        "scope": "project",
        "purpose": "A purpose.",
        "applies_when": ["a trigger"],
        "do_not_apply_when": ["a stop"],
        "activation_rules": ACTIVATION,
        "created_from": ["manual:test"],
    }
    kwargs.update(overrides)
    return store.create(**kwargs)


def moved(record: SkillRecord, **changes: Any) -> SkillRecord:
    document = dict(record.raw)
    if "scope" in changes:
        document["classification"] = {**document["classification"], "scope": changes["scope"]}
    if "slug" in changes:
        document["slug"] = changes["slug"]
    return SkillRecord.from_document(document, source=record.id)


# --- every legal scope transition is refused -------------------------------


@pytest.mark.parametrize("origin", SKILL_SCOPES)
@pytest.mark.parametrize("target", SKILL_SCOPES)
def test_every_scope_change_is_refused(skills: SkillStore, origin: str, target: str) -> None:
    """All nine ordered pairs. The three identity pairs must still save."""
    skill = make(skills, scope=origin)
    candidate = moved(skill, scope=target)

    if origin == target:
        assert skills.save(candidate).scope == origin
        return

    with pytest.raises(LocationInvariantError) as excinfo:
        skills.save(candidate)
    message = str(excinfo.value)
    assert origin in message
    assert target in message


def test_slug_change_is_refused(skills: SkillStore) -> None:
    skill = make(skills)
    with pytest.raises(LocationInvariantError, match="slug"):
        skills.save(moved(skill, slug="a-different-slug"))


def test_scope_and_slug_changed_together_names_both(skills: SkillStore) -> None:
    skill = make(skills)
    with pytest.raises(LocationInvariantError) as excinfo:
        skills.save(moved(skill, scope="core", slug="a-different-slug"))
    message = str(excinfo.value)
    assert "scope" in message
    assert "slug" in message


def test_the_refusal_explains_why_rather_than_just_denying(skills: SkillStore) -> None:
    skill = make(skills)
    with pytest.raises(LocationInvariantError) as excinfo:
        skills.save(moved(skill, scope="core"))
    message = str(excinfo.value)
    assert "immutable" in message
    assert "overwrite" in message


# --- ordinary operations keep working --------------------------------------


def test_an_unchanged_identity_saves(skills: SkillStore) -> None:
    skill = make(skills)
    assert skills.save(skill).id == skill.id


def test_update_still_edits_behavioural_fields(skills: SkillStore) -> None:
    skill = make(skills)
    assert skills.update(skill.id, procedure=["step one"]).procedure == ("step one",)


def test_attach_evidence_still_works(skills: SkillStore) -> None:
    skill = make(skills)
    assert skills.attach_evidence(skill.id, knowledge=["K-0001"]).knowledge_ids == ("K-0001",)


def test_maturity_protection_is_unchanged_not_reimplemented(skills: SkillStore) -> None:
    """The existing EDITABLE_FIELDS rule still owns this, not the new check."""
    skill = make(skills)
    with pytest.raises(ValidationError, match="may not be edited directly"):
        skills.update(skill.id, classification={"scope": "core", "maturity": "trusted"})


def test_promotion_still_persists_through_save(layout: Layout, skills: SkillStore) -> None:
    skill = make(skills)
    promoted = PromotionEngine(layout).promote(
        skill.id, "candidate", reason="applicable", actor=ACTOR
    )
    assert promoted.maturity == "candidate"
    assert SkillStore(Layout(root=layout.root)).get(skill.id).maturity == "candidate"


def test_deprecation_still_persists_through_save(layout: Layout, skills: SkillStore) -> None:
    skill = make(skills)
    engine = PromotionEngine(layout)
    engine.promote(skill.id, "candidate", reason="applicable", actor=ACTOR)
    deprecated = engine.deprecate(skill.id, reason="superseded", actor=ACTOR)
    assert deprecated.maturity == "deprecated"


def test_create_still_refuses_a_duplicate_scope_slug_pair(skills: SkillStore) -> None:
    make(skills)
    with pytest.raises(ValidationError, match="already exists"):
        make(skills)


def test_the_same_slug_in_a_different_scope_is_still_allowed(skills: SkillStore) -> None:
    make(skills, scope="project")
    assert make(skills, scope="core").scope == "core"


def test_an_explicit_slug_is_honoured_and_canonical(layout: Layout, skills: SkillStore) -> None:
    skill = make(skills, slug="explicit-slug")
    assert skill.slug == "explicit-slug"
    assert skills.registry.path_of(skill.id) == layout.skill_path("project", "explicit-slug")


# --- rejection writes nothing ----------------------------------------------


def test_a_rejected_save_leaves_the_filesystem_byte_identical(
    layout: Layout, skills: SkillStore
) -> None:
    """Filesystem state, not just Python objects."""
    skill = make(skills)
    before = fingerprint(layout.root)

    for change in ({"scope": "core"}, {"slug": "other"}, {"scope": "discovered", "slug": "x"}):
        with pytest.raises(LocationInvariantError):
            skills.save(moved(skill, **change))

    assert fingerprint(layout.root) == before


def test_a_rejected_save_creates_no_new_directory(layout: Layout, skills: SkillStore) -> None:
    skill = make(skills)
    with pytest.raises(LocationInvariantError):
        skills.save(moved(skill, scope="core"))
    assert not (layout.skills_dir / "core" / "target-skill").exists()


# --- canonical path computation --------------------------------------------


def test_layout_is_the_single_authority_for_skill_paths(layout: Layout) -> None:
    assert layout.relative_skill_path("project", "s") == f"project/s/{SKILL_FILENAME}"
    assert layout.skill_path("project", "s") == layout.skills_dir / "project" / "s" / SKILL_FILENAME


def test_the_store_delegates_rather_than_re_deriving(layout: Layout, skills: SkillStore) -> None:
    assert skills.relative_skill_path("core", "x") == layout.relative_skill_path("core", "x")


@pytest.mark.parametrize(
    "relative",
    ["project/s/skill.yaml", "core/s/skill.yaml", "discovered/s/skill.yaml"],
)
def test_canonical_paths_round_trip(layout: Layout, relative: str) -> None:
    parsed = layout.parse_relative_skill_path(relative)
    assert parsed is not None
    assert layout.relative_skill_path(*parsed) == relative


@pytest.mark.parametrize(
    "relative",
    [
        "project/s/other.yaml",  # wrong filename
        "unknown/s/skill.yaml",  # not a scope
        "project/skill.yaml",  # too shallow
        "project/a/b/skill.yaml",  # too deep
        "records/K-0001.yaml",  # another domain's shape
    ],
)
def test_non_canonical_paths_are_rejected_by_the_parser(layout: Layout, relative: str) -> None:
    assert layout.parse_relative_skill_path(relative) is None


def test_an_empty_slug_is_refused(layout: Layout) -> None:
    """Now refused by the canonical slug grammar (DEC-0013).

    Previously a bare ValueError; an empty slug is simply one case of a
    non-canonical component, so it raises the same SkillKernelError as every
    other malformed slug.
    """
    with pytest.raises(ValidationError, match="not a canonical skill slug"):
        layout.skill_path("project", "")


def test_an_unknown_scope_is_refused(layout: Layout) -> None:
    with pytest.raises(ValueError, match="unknown skill scope"):
        layout.relative_skill_path("deprecated", "s")


# --- doctor detects pre-existing corruption --------------------------------


def corrupt(layout: Layout, skill_id: str, **changes: Any) -> None:
    """Hand-edit a stored record, bypassing every public write path."""
    path = SkillStore(layout).registry.path_of(skill_id)
    document = load_yaml_file(path)
    if "scope" in changes:
        document["classification"]["scope"] = changes["scope"]
    if "slug" in changes:
        document["slug"] = changes["slug"]
    write_yaml_file(path, document)


def test_doctor_detects_a_corrupted_scope(layout: Layout, skills: SkillStore) -> None:
    skill = make(skills)
    corrupt(layout, skill.id, scope="core")
    report = run_doctor(Layout(root=layout.root))
    assert any(f.code == "skill-location" for f in report.errors)


def test_doctor_detects_a_corrupted_slug(layout: Layout, skills: SkillStore) -> None:
    skill = make(skills)
    corrupt(layout, skill.id, slug="renamed")
    report = run_doctor(Layout(root=layout.root))
    assert any(f.code == "skill-location" for f in report.errors)


def test_doctor_reports_both_the_stored_and_declared_locations(
    layout: Layout, skills: SkillStore
) -> None:
    skill = make(skills)
    corrupt(layout, skill.id, scope="core")
    finding = next(
        f for f in run_doctor(Layout(root=layout.root)).errors if f.code == "skill-location"
    )
    assert "project/target-skill" in finding.message
    assert "core/target-skill" in finding.message


def test_doctor_detects_two_skills_registered_at_one_location(
    layout: Layout, skills: SkillStore
) -> None:
    """Representable only by hand now that persistence refuses to create it."""
    first = make(skills)
    second = make(skills, name="Other Skill")
    document = load_yaml_file(skills.registry.index_file)
    for entry in document["entries"]:
        if entry["id"] == second.id:
            entry["path"] = f"project/target-skill/{SKILL_FILENAME}"
    write_yaml_file(skills.registry.index_file, document)

    report = run_doctor(Layout(root=layout.root))
    assert any("more than one skill" in f.message for f in report.errors), [
        str(f) for f in report.errors
    ]
    assert first.id != second.id


def test_doctor_is_read_only_on_a_corrupted_workspace(layout: Layout, skills: SkillStore) -> None:
    skill = make(skills)
    corrupt(layout, skill.id, scope="core")
    before = fingerprint(layout.root)
    run_doctor(Layout(root=layout.root))
    assert fingerprint(layout.root) == before


def test_a_healthy_workspace_reports_no_location_errors(layout: Layout, skills: SkillStore) -> None:
    make(skills, scope="project")
    make(skills, scope="core", name="Core Skill")
    report = run_doctor(Layout(root=layout.root))
    assert [f for f in report.errors if f.code == "skill-location"] == []


# --- the guard stayed skill-specific ---------------------------------------


def test_experiment_revise_still_changes_its_registered_path(layout: Layout) -> None:
    """Proof the invariant did not become a generic registry rule.

    Experiments legitimately re-register the same id at a new path on revise.
    A generic "an id may not change path" guard would have broken this.
    """
    store = ExperimentStore(layout)
    definition = store.add(
        title="t",
        hypothesis="h",
        independent_variable="v",
        control={"label": "c", "description": "c"},
        treatment={"label": "t", "description": "t"},
        corpus={"id": "c", "description": "d", "cases": ["c1"]},
        scorer={"name": "s", "version": "1", "deterministic": True},
        primary_metric={"name": "m", "direction": "maximize", "description": None},
        pass_threshold=0.9,
        failure_threshold=0.5,
        project="p",
    )
    before = store.registry.entry(definition.id).path
    store.freeze(definition.id)
    store.revise(definition.id, changes={"pass_threshold": 0.8}, reason="recalibrated")
    after = store.registry.entry(definition.id).path

    assert before != after
    assert after.endswith("v2.yaml")
    assert run_doctor(Layout(root=layout.root)).is_complete
