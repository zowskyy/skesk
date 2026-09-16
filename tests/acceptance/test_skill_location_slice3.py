"""Vertical Slice 3: a skill's declared identity must match where it is stored.

A skill's canonical location is derived from two declared fields —
``classification.scope`` and ``slug``. Before this slice, ordinary persistence
accepted a change to either while leaving the record registered at its original
path. The declaration and the location then disagreed, which was not merely
untidy: the ``(scope, slug)`` pair appeared free, a second skill could be
created at the same physical path, and the first skill's ``skill.yaml`` and
``history.yaml`` were silently overwritten. Its provenance was destroyed, and
nothing noticed until a later read raised ``IntegrityError``.

The acceptance question:

    Can a persisted skill ever successfully acquire a declared scope or slug
    inconsistent with its canonical stored location — and if pre-existing
    corruption contains such a mismatch, does SkillKernel detect it before
    another skill can silently occupy the same location?

Note on what is *not* asserted here. An earlier draft reproduced the destructive
sequence and asserted it succeeded. That evidence belongs in the slice's record,
not in a permanent suite: a test whose passing condition requires corruption
becomes a trap the moment someone fixes the defect it documents. The historical
reproduction was run and captured separately; this module asserts only the
behaviour the system should have.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
from skillkernel.core.errors import SkillKernelError
from skillkernel.core.paths import Layout
from skillkernel.core.yamlio import load_yaml_file, write_yaml_file
from skillkernel.project.bootstrap import initialize
from skillkernel.skills.model import SkillRecord
from skillkernel.skills.store import SkillStore
from skillkernel.validation.doctor import run_doctor

pytestmark = pytest.mark.acceptance

ACTIVATION = {"require_any": ["trigger"], "require_all": [], "exclude_any": ["stop"]}


def fingerprint(root: Path) -> dict[str, str]:
    """Hash every file under ``root``.

    Refuses an empty or missing tree. ``Path.rglob`` yields nothing for a
    directory that does not exist, so without this guard a before/after
    comparison would compare ``{} == {}`` and pass while proving nothing — a
    vacuous pass that actually occurred during VS2.
    """
    assert root.is_dir(), f"{root} is not a directory; nothing to fingerprint"
    digests: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            digests[path.relative_to(root).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    assert digests, f"{root} contains no files; a comparison would be vacuous"
    return digests


@pytest.fixture
def workspace(tmp_path: Path, frozen_now: str) -> Layout:
    return initialize(tmp_path / "ws", project_name="slice3")


def make_skill(layout: Layout, *, name: str = "Target Skill", scope: str = "project") -> Any:
    return SkillStore(layout).create(
        name=name,
        scope=scope,
        purpose="Original purpose.",
        applies_when=["a trigger is present"],
        do_not_apply_when=["a stop signal is present"],
        activation_rules=ACTIVATION,
        created_from=["manual:slice3"],
    )


def mutated(record: SkillRecord, **changes: Any) -> SkillRecord:
    """Build a record whose location-bearing identity has been altered."""
    document = dict(record.raw)
    if "scope" in changes:
        document["classification"] = {**document["classification"], "scope": changes["scope"]}
    if "slug" in changes:
        document["slug"] = changes["slug"]
    return SkillRecord.from_document(document, source=record.id)


# --- persistence refuses to move a skill's identity ------------------------


def test_scope_mutation_through_ordinary_persistence_is_rejected(workspace: Layout) -> None:
    store = SkillStore(workspace)
    skill = make_skill(workspace)

    with pytest.raises(SkillKernelError) as excinfo:
        store.save(mutated(skill, scope="core"))

    message = str(excinfo.value).lower()
    assert "scope" in message
    assert "core" in message


def test_slug_mutation_through_ordinary_persistence_is_rejected(workspace: Layout) -> None:
    store = SkillStore(workspace)
    skill = make_skill(workspace)

    with pytest.raises(SkillKernelError) as excinfo:
        store.save(mutated(skill, slug="renamed-slug"))

    assert "slug" in str(excinfo.value).lower()


def test_simultaneous_scope_and_slug_mutation_is_rejected(workspace: Layout) -> None:
    store = SkillStore(workspace)
    skill = make_skill(workspace)

    with pytest.raises(SkillKernelError):
        store.save(mutated(skill, scope="core", slug="renamed-slug"))


def test_a_rejected_mutation_writes_nothing(workspace: Layout) -> None:
    """The check must precede the first write, not undo one afterwards."""
    store = SkillStore(workspace)
    skill = make_skill(workspace)
    before = fingerprint(workspace.root)

    for change in ({"scope": "core"}, {"slug": "renamed-slug"}):
        with pytest.raises(SkillKernelError):
            store.save(mutated(skill, **change))

    assert fingerprint(workspace.root) == before


def test_an_unchanged_identity_still_persists_normally(workspace: Layout) -> None:
    """The guard must not break ordinary editing."""
    store = SkillStore(workspace)
    skill = make_skill(workspace)

    updated = store.update(skill.id, purpose="A revised purpose.")
    assert updated.purpose == "A revised purpose."
    assert SkillStore(Layout(root=workspace.root)).get(skill.id).purpose == "A revised purpose."


def test_the_collision_is_unreachable_through_public_apis(workspace: Layout) -> None:
    """The destructive sequence's first step is now refused, so it cannot start."""
    store = SkillStore(workspace)
    first = make_skill(workspace)

    with pytest.raises(SkillKernelError):
        store.save(mutated(first, scope="core"))

    # The pair never became falsely available, so a second create is still refused.
    fresh = SkillStore(Layout(root=workspace.root))
    assert fresh.find_by_slug("project", "target-skill") is not None
    with pytest.raises(SkillKernelError):
        make_skill(workspace)

    reloaded = fresh.get(first.id)
    assert reloaded.scope == "project"
    assert reloaded.purpose == "Original purpose."


# --- pre-existing corruption is detected, never repaired -------------------


def corrupt_scope_on_disk(layout: Layout, skill_id: str, new_scope: str) -> None:
    """Hand-edit a stored record, bypassing every public write path.

    This is how a workspace corrupted before this slice — or by a stray editor —
    reaches the kernel. It must be detectable even though it can no longer be
    produced through the API.
    """
    path = SkillStore(layout).registry.path_of(skill_id)
    document = load_yaml_file(path)
    document["classification"]["scope"] = new_scope
    write_yaml_file(path, document)


def test_doctor_detects_a_pre_existing_mismatch(workspace: Layout) -> None:
    skill = make_skill(workspace)
    corrupt_scope_on_disk(workspace, skill.id, "core")

    report = run_doctor(Layout(root=workspace.root))

    assert report.has_errors, "a scope/path mismatch must be an ERROR"
    assert any(
        skill.id in finding.location or skill.id in finding.message for finding in report.errors
    )
    assert any(
        "location" in finding.code or "location" in finding.message.lower()
        for finding in report.errors
    )


def test_doctor_leaves_a_corrupted_workspace_byte_identical(workspace: Layout) -> None:
    """Diagnosis must never mutate the thing being diagnosed."""
    skill = make_skill(workspace)
    corrupt_scope_on_disk(workspace, skill.id, "core")
    before = fingerprint(workspace.root)

    run_doctor(Layout(root=workspace.root))

    assert fingerprint(workspace.root) == before


def test_doctor_detects_the_mismatch_before_a_collision_can_occur(workspace: Layout) -> None:
    """Detection has to come first to be worth anything.

    The destructive sequence needed the mismatch to go unnoticed. Here doctor
    reports it while the original skill is still intact.
    """
    skill = make_skill(workspace)
    corrupt_scope_on_disk(workspace, skill.id, "core")

    report = run_doctor(Layout(root=workspace.root))
    assert report.has_errors

    store = SkillStore(Layout(root=workspace.root))
    assert store.get(skill.id).purpose == "Original purpose."
    assert store.registry.path_of(skill.id).is_file()
