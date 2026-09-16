"""Installing a bundle: what is adopted, what is earned, and what is refused.

Two properties are under test throughout.

**Nothing is inherited.** A bundle contributes a definition. The identifier,
the maturity, the history, the evaluation evidence and the fingerprint are all
produced by the receiving workspace, and no arrangement of bundle content can
shorten that path.

**A refusal costs nothing.** Every refusal below is checked against a
filesystem fingerprint taken before the attempt, including the registry's
identifier counter -- so "refused" means the workspace is byte-identical
afterwards, not merely that an exception was raised.
"""

from __future__ import annotations

import dataclasses
import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from skillkernel.bundles.catalog import load_bundle
from skillkernel.bundles.installer import install_bundle, install_bundle_by_id
from skillkernel.bundles.model import SOURCE_FIELD, Bundle
from skillkernel.core.errors import (
    GateError,
    SkillKernelError,
    UnsafeOperationError,
    ValidationError,
)
from skillkernel.core.paths import Layout
from skillkernel.evaluation.runner import evaluate_skill
from skillkernel.evaluation.suite import load_evaluation_suite
from skillkernel.promotion.engine import PromotionEngine
from skillkernel.skills.store import SkillStore
from skillkernel.validation.doctor import run_doctor

Build = Callable[..., Path]
Manifest = dict[str, Any]

PACKAGED = "two-method-escalation"


# --- helpers ---------------------------------------------------------------


def fingerprint(root: Path) -> dict[str, str]:
    """Hash every file under ``root``.

    Refuses an empty or missing tree: ``rglob`` yields nothing for a directory
    that does not exist, so without this guard a before/after comparison would
    compare ``{} == {}`` and pass while proving nothing at all.
    """
    assert root.is_dir(), f"{root} is not a directory; nothing to fingerprint"
    digests = {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
    assert digests, f"{root} contains no files; a comparison would be vacuous"
    return digests


def assert_refuses_without_writing(
    layout: Layout, attempt: Callable[[], object], expected: type[Exception], match: str
) -> None:
    before = fingerprint(layout.root)
    with pytest.raises(expected, match=match):
        attempt()
    assert fingerprint(layout.root) == before, (
        "the refusal left the workspace changed; a rejected install must not "
        "write anything, not even a burned identifier"
    )


def demo(make_bundle: Build) -> Bundle:
    return load_bundle("demo-skill", root=make_bundle())


# --- a successful install --------------------------------------------------


def test_the_packaged_bundle_installs(layout: Layout) -> None:
    result = install_bundle_by_id(layout, PACKAGED)
    assert result.record.slug == PACKAGED
    assert result.record.scope == "core"
    assert result.relative_path == f"core/{PACKAGED}/skill.yaml"
    assert (layout.root / "skills" / "core" / PACKAGED / "skill.yaml").is_file()


def test_the_identifier_is_allocated_locally(layout: Layout, skills: SkillStore) -> None:
    """A bundle carries no identifier, so the workspace's counter decides."""
    skills.create(name="A local skill", scope="project")
    result = install_bundle_by_id(layout, PACKAGED)
    assert result.record.id == "SKILL-0002"


def test_the_definition_is_adopted_in_full(layout: Layout) -> None:
    bundle = load_bundle(PACKAGED)
    record = install_bundle(layout, bundle).record
    for field, value in bundle.definition_fields().items():
        actual = getattr(record, field)
        actual = list(actual) if isinstance(actual, tuple) else actual
        assert actual == value, f"{field} was not adopted from the bundle"


def test_the_skill_starts_where_every_other_skill_starts(layout: Layout) -> None:
    record = install_bundle_by_id(layout, PACKAGED).record
    assert record.maturity == "observed"
    assert record.confidence == "low"
    assert record.evidence == {"experiments": [], "knowledge": [], "records": []}


def test_the_history_records_only_the_local_creation(layout: Layout) -> None:
    record = install_bundle_by_id(layout, PACKAGED).record
    assert SkillStore(layout).maturity_path(record.id) == ["observed"]


def test_source_provenance_has_exactly_three_fields(layout: Layout) -> None:
    bundle = load_bundle(PACKAGED)
    record = install_bundle(layout, bundle).record
    assert record.provenance[SOURCE_FIELD] == {
        "bundle_id": bundle.bundle_id,
        "bundle_version": bundle.bundle_version,
        "content_hash": bundle.content_hash,
    }


def test_local_provenance_is_kept_separate_from_source_provenance(layout: Layout) -> None:
    record = install_bundle_by_id(layout, PACKAGED).record
    assert record.provenance["created_by"] == "bundled"
    assert record.provenance["created_from"] == [f"bundle:{PACKAGED}@1.0.0"]
    assert record.provenance["discovery_key"] is None
    assert record.project_scope == {"origin_project": None, "validated_in_projects": []}


def test_the_evaluation_suite_is_bound_to_the_local_identity(layout: Layout) -> None:
    """The bundle ships cases and thresholds. The workspace owns the suite."""
    result = install_bundle_by_id(layout, PACKAGED)
    suite = load_evaluation_suite(layout, result.record.id)
    assert suite.skill_id == result.record.id
    assert len(suite.cases) == result.positive_cases + result.negative_cases


def test_the_installed_skill_survives_a_reload(layout: Layout) -> None:
    installed = install_bundle_by_id(layout, PACKAGED).record
    reloaded = SkillStore(Layout(root=layout.root)).get(installed.id)
    assert reloaded.raw == installed.raw


def test_doctor_is_clean_after_an_install(layout: Layout) -> None:
    install_bundle_by_id(layout, PACKAGED)
    report = run_doctor(layout)
    assert report.is_complete
    assert not report.has_errors, [str(f) for f in report.sorted_findings()]


# --- the content hash ------------------------------------------------------


def test_the_content_hash_does_not_depend_on_the_workspace(tmp_path: Path) -> None:
    from skillkernel.project.bootstrap import initialize

    hashes = {
        install_bundle_by_id(initialize(tmp_path / name, project_name=name), PACKAGED).content_hash
        for name in ("one", "two")
    }
    assert len(hashes) == 1, "the portable hash varied between workspaces"


def test_the_recorded_hash_is_the_bundle_hash(layout: Layout) -> None:
    result = install_bundle_by_id(layout, PACKAGED)
    assert (
        result.record.provenance[SOURCE_FIELD]["content_hash"] == load_bundle(PACKAGED).content_hash
    )


# --- the lifecycle that follows --------------------------------------------


def test_the_bundle_definition_alone_satisfies_the_candidate_gate(layout: Layout) -> None:
    record = install_bundle_by_id(layout, PACKAGED).record
    promoted = PromotionEngine(layout).promote(
        record.id, "candidate", reason="The installed definition is complete.", actor="test"
    )
    assert promoted.maturity == "candidate"


def test_evaluation_produces_locally_earned_evidence(layout: Layout) -> None:
    record = install_bundle_by_id(layout, PACKAGED).record
    PromotionEngine(layout).promote(
        record.id, "candidate", reason="The installed definition is complete.", actor="test"
    )
    report = evaluate_skill(layout, record.id, project="fixture-project")
    assert report.verdict == "pass"
    assert report.evidence_id is not None
    assert report.skill_fingerprint == SkillStore(layout).get(record.id).fingerprint()


def test_experimental_remains_out_of_reach(layout: Layout) -> None:
    """A bundle must never be able to ship the experiment evidence a gate wants."""
    record = install_bundle_by_id(layout, PACKAGED).record
    engine = PromotionEngine(layout)
    engine.promote(record.id, "candidate", reason="Definition complete.", actor="test")
    evaluate_skill(layout, record.id, project="fixture-project")
    with pytest.raises(GateError, match="experiment evidence"):
        engine.promote(record.id, "experimental", reason="Try it.", actor="test")


# --- refusals --------------------------------------------------------------


def test_installing_the_same_bundle_twice_is_refused(layout: Layout) -> None:
    install_bundle_by_id(layout, PACKAGED)
    assert_refuses_without_writing(
        layout, lambda: install_bundle_by_id(layout, PACKAGED), ValidationError, "already exists"
    )


def test_a_renamed_bundle_with_the_same_identity_is_refused(layout: Layout) -> None:
    """Renaming the slug must not turn a re-install into a second copy."""
    bundle = load_bundle(PACKAGED)
    install_bundle(layout, bundle)
    renamed = dataclasses.replace(bundle, manifest={**bundle.manifest, "slug": "escalation-copy"})
    assert_refuses_without_writing(
        layout, lambda: install_bundle(layout, renamed), ValidationError, "already installed"
    )


def test_the_same_content_under_another_bundle_id_is_refused(layout: Layout) -> None:
    """Aliasing: identical content installed twice would make provenance ambiguous."""
    bundle = load_bundle(PACKAGED)
    install_bundle(layout, bundle)
    aliased = dataclasses.replace(
        bundle,
        bundle_id="escalation-fork",
        manifest={**bundle.manifest, "bundle_id": "escalation-fork", "slug": "escalation-fork"},
    )
    assert_refuses_without_writing(
        layout, lambda: install_bundle(layout, aliased), ValidationError, "same content hash"
    )


def test_a_collision_with_a_locally_authored_skill_is_refused(
    layout: Layout, skills: SkillStore
) -> None:
    skills.create(name="Two method escalation", scope="core")
    assert_refuses_without_writing(
        layout, lambda: install_bundle_by_id(layout, PACKAGED), ValidationError, "already exists"
    )


def test_an_unmanaged_directory_in_the_way_is_refused(layout: Layout) -> None:
    """A directory no skill owns is residue, and residue is never written into."""
    stray = layout.root / "skills" / "core" / PACKAGED
    stray.mkdir(parents=True)
    (stray / "skill.yaml").write_text("# left behind\n", encoding="utf-8")
    assert_refuses_without_writing(
        layout,
        lambda: install_bundle_by_id(layout, PACKAGED),
        UnsafeOperationError,
        "unmanaged directory",
    )


def test_an_unknown_bundle_writes_nothing(layout: Layout) -> None:
    assert_refuses_without_writing(
        layout,
        lambda: install_bundle_by_id(layout, "no-such-bundle"),
        SkillKernelError,
        "no bundle",
    )


def test_a_forged_non_canonical_slug_is_refused_by_the_installer(layout: Layout) -> None:
    """Defence in depth: the catalog refuses it too, but the installer never trusts that."""
    bundle = load_bundle(PACKAGED)
    forged = dataclasses.replace(bundle, manifest={**bundle.manifest, "slug": "../../escape"})
    assert_refuses_without_writing(
        layout, lambda: install_bundle(layout, forged), ValidationError, "canonical skill slug"
    )


def test_a_forged_scope_is_refused(layout: Layout) -> None:
    bundle = load_bundle(PACKAGED)
    forged = dataclasses.replace(bundle, manifest={**bundle.manifest, "scope": "elsewhere"})
    assert_refuses_without_writing(
        layout, lambda: install_bundle(layout, forged), ValidationError, "unknown scope"
    )


def test_a_definition_that_would_not_validate_burns_no_identifier(layout: Layout) -> None:
    """The dry run runs before ``allocate_id``, so a bad definition costs nothing."""
    bundle = load_bundle(PACKAGED)
    forged = dataclasses.replace(bundle, manifest={**bundle.manifest, "name": ""})
    before = SkillStore(layout).registry.load_index().next_sequence
    assert_refuses_without_writing(
        layout, lambda: install_bundle(layout, forged), ValidationError, "name"
    )
    assert SkillStore(layout).registry.load_index().next_sequence == before


def test_a_duplicate_case_id_is_refused_before_the_skill_exists(
    layout: Layout, make_bundle: Build, bundle_positive: list[dict[str, Any]]
) -> None:
    """``write_evaluation_suite`` would catch this, but only after the skill was created."""
    clash = [{**bundle_positive[0], "case_id": "demo-no"}]
    bundle = load_bundle("demo-skill", root=make_bundle(positive=clash))
    assert_refuses_without_writing(
        layout, lambda: install_bundle(layout, bundle), ValidationError, "more than once"
    )
    assert SkillStore(layout).ids() == []


def test_a_synthetic_bundle_installs_alongside_the_packaged_one(
    layout: Layout, make_bundle: Build
) -> None:
    """Two different bundles coexist; only identity collisions are refused."""
    install_bundle_by_id(layout, PACKAGED)
    installed = install_bundle(layout, demo(make_bundle)).record
    assert {record.slug for record in SkillStore(layout).all()} == {PACKAGED, "demo-skill"}
    assert installed.provenance[SOURCE_FIELD]["bundle_id"] == "demo-skill"
