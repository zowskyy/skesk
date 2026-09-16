"""A registry may only write inside the domain directory it owns.

Found by the bounded structural search that followed the ``case_id`` repair,
looking for the same shape: *guard the parent, then append an untrusted
component, then write*.

``Registry.resolve`` joined an index-supplied relative path onto its domain
directory and then asked :meth:`Layout.require_inside` whether the result was in
the workspace. That is the wrong question. ``knowledge/../skills/core/x.yaml`` is
inside the workspace and outside the knowledge domain, so a hand-edited index
entry could redirect one domain's record write into another domain's tree --
reproduced as a knowledge record landing in ``skills/core/``.

This is the same defect as the ``case_id`` escape, one level up: the boundary
that mattered was the *owning root*, not the repository. Layer B (DEC-0017) is
the general answer, so the fix is to ask ``require_within`` instead.

Reachability is lower than the bundle case -- it needs write access to the index
file rather than packaged content -- but the invariant is identical, and an
invariant with a known hole is not an invariant.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml
from skillkernel.core.errors import UnsafeOperationError
from skillkernel.core.paths import Layout
from skillkernel.experiments.store import ExperimentStore
from skillkernel.knowledge.store import KnowledgeStore
from skillkernel.registry import Registry
from skillkernel.skills.store import SkillStore, skills_registry

# Each stays inside the workspace while leaving the domain directory. The
# workspace guard cannot see any of them.
CROSS_DOMAIN_PATHS = [
    "../skills/core/HIJACKED.yaml",
    "../evidence/artifacts/HIJACKED.yaml",
    "../../skillkernel.yaml",
    "records/../../docs/HIJACKED.yaml",
    "./../observations/HIJACKED.yaml",
]

# Already refused before this repair, and must stay refused.
ESCAPES_WORKSPACE = ["../../../OUTSIDE.yaml", "/etc/passwd"]


def fingerprint(root: Path) -> dict[str, str]:
    assert root.is_dir(), f"{root} is not a directory"
    digests = {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
    assert digests, f"{root} contains no files; a comparison would be vacuous"
    return digests


def retarget(registry: Registry, record_id: str, path: str) -> None:
    """Rewrite one index entry's path, modelling a hand edit of the index file."""
    index_file = registry.index_file
    document = yaml.safe_load(index_file.read_text(encoding="utf-8"))
    row = next(entry for entry in document["entries"] if entry["id"] == record_id)
    row["path"] = path
    index_file.write_text(yaml.safe_dump(document), encoding="utf-8")


# --- what must keep working -----------------------------------------------


def test_every_domain_resolves_its_own_legitimate_paths(layout: Layout) -> None:
    """The repair must not narrow any path a domain legitimately stores."""
    skills = SkillStore(layout)
    skill = skills.create(name="Legitimate skill", scope="core")
    assert skills.registry.resolve("core/legitimate-skill/skill.yaml").is_relative_to(
        layout.skills_dir
    )
    assert skills.registry.path_of(skill.id).is_file()

    knowledge = KnowledgeStore(layout)
    record = knowledge.add(
        statement="A testable claim about the world.", scope="project", source_type="research"
    )
    assert knowledge.registry.path_of(record.id).is_file()

    experiments = ExperimentStore(layout)
    assert experiments.registry.resolve("definitions/EXP-0001/v1.yaml").is_relative_to(
        layout.experiments_dir
    )


def test_the_default_record_path_still_resolves(layout: Layout) -> None:
    registry = skills_registry(layout)
    resolved = registry.resolve(registry.default_record_path("SKILL-0001"))
    assert resolved.is_relative_to(layout.skills_dir)


# --- the defect -----------------------------------------------------------


@pytest.mark.parametrize("path", CROSS_DOMAIN_PATHS)
def test_resolve_refuses_a_path_outside_its_domain(layout: Layout, path: str) -> None:
    """Inside the workspace is not the same question as inside the domain."""
    registry = KnowledgeStore(layout).registry
    with pytest.raises(UnsafeOperationError):
        registry.resolve(path)


@pytest.mark.parametrize("path", ESCAPES_WORKSPACE)
def test_resolve_still_refuses_a_path_outside_the_workspace(layout: Layout, path: str) -> None:
    """The pre-existing workspace guard must not be weakened by the new one."""
    registry = KnowledgeStore(layout).registry
    with pytest.raises(UnsafeOperationError):
        registry.resolve(path)


@pytest.mark.parametrize("path", CROSS_DOMAIN_PATHS)
def test_a_tampered_index_cannot_redirect_a_write(layout: Layout, path: str) -> None:
    """The reproduction: a knowledge record was made to land in ``skills/core/``."""
    knowledge = KnowledgeStore(layout)
    record = knowledge.add(
        statement="A testable claim about the world.", scope="project", source_type="research"
    )
    retarget(knowledge.registry, record.id, path)

    before = fingerprint(layout.root)
    reloaded = KnowledgeStore(layout)
    with pytest.raises(UnsafeOperationError):
        reloaded.registry.put(record.id, dict(record.raw), summary={})

    after = fingerprint(layout.root)
    assert after == before, "a refused write still changed the workspace"
    assert not (layout.skills_dir / "core" / "HIJACKED.yaml").exists()


def test_a_tampered_index_cannot_redirect_a_read(layout: Layout) -> None:
    """Reads resolve through the same authority, so they inherit the boundary."""
    knowledge = KnowledgeStore(layout)
    record = knowledge.add(
        statement="A testable claim about the world.", scope="project", source_type="research"
    )
    retarget(knowledge.registry, record.id, "../skills/core/HIJACKED.yaml")

    with pytest.raises(UnsafeOperationError):
        KnowledgeStore(layout).registry.path_of(record.id)


def test_the_skills_domain_is_bounded_too(layout: Layout) -> None:
    """Skills already had DEC-0011 on declared identity; the registry needed it as well."""
    skills = SkillStore(layout)
    skill = skills.create(name="Bounded skill", scope="core")
    retarget(skills.registry, skill.id, "../knowledge/records/HIJACKED.yaml")

    before = fingerprint(layout.root)
    with pytest.raises(UnsafeOperationError):
        SkillStore(layout).registry.resolve("../knowledge/records/HIJACKED.yaml")
    assert fingerprint(layout.root) == before
