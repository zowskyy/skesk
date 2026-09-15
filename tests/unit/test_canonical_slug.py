"""A slug is a single, well-formed path component.

A skill's canonical location is ``skills/<scope>/<slug>/skill.yaml``. VS3 made
the *registered* path agree with the *declared* identity, but did not constrain
what a declared identity may contain. A slug of ``../../evil`` therefore
produced a registered path of ``project/../../evil/skill.yaml`` that equalled
its own canonical form — so the VS3 check reported no mismatch — while the file
landed outside ``skills/`` entirely, and ``doctor`` saw nothing.

The grammar below is not a new restriction. It is exactly what ``slugify()``
already produces and what every slug in this repository already satisfies:

    ^[a-z0-9]+(?:-[a-z0-9]+)*$

Rejecting a malformed component up front is stronger than joining arbitrary
input and inspecting the result afterwards, because the dangerous path is never
constructed at all.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
from skillkernel.core.errors import SkillKernelError
from skillkernel.core.paths import Layout, is_canonical_slug, validate_slug
from skillkernel.core.yamlio import load_yaml_file, write_yaml_file
from skillkernel.skills.store import SkillStore
from skillkernel.utils.text import slugify
from skillkernel.validation.doctor import run_doctor

ACTIVATION = {"require_any": ["t"], "require_all": [], "exclude_any": []}

# Every slug the repository actually uses or generates today.
EXISTING_SLUGS = [
    "target-skill",
    "two-method-escalation",
    "stabilise-import-ordering",
    "sample-skill",
    "deterministic-frame-rendering",
    "a-different-slug",
    "explicit-slug",
    "renamed",
    "renamed-slug",
    "core-one",
    "shared-name",
    "a-b-test",
    "naive-cafe",
    "x",
    "a1",
    "9lives",
]

MALFORMED = [
    ("..", "relative-parent"),
    (".", "relative-self"),
    ("../x", "traversal-prefix"),
    ("../../x", "traversal-deep"),
    ("x/../y", "traversal-embedded"),
    ("/absolute", "absolute"),
    ("a/b", "forward-separator"),
    ("a\\b", "backslash-separator"),
    ("", "empty"),
    ("   ", "whitespace-only"),
    (" padded", "leading-space"),
    ("padded ", "trailing-space"),
    (".hidden", "leading-dot"),
    ("trailing.", "trailing-dot"),
    ("a.b", "embedded-dot"),
    ("-leading", "leading-hyphen"),
    ("trailing-", "trailing-hyphen"),
    ("double--hyphen", "repeated-hyphen"),
    ("C:\\windows", "drive-letter"),
    ("C:", "bare-drive"),
    ("naïve", "unicode"),
    ("UPPER", "uppercase"),
    ("under_score", "underscore"),
    ("has space", "embedded-space"),
    ("nul\x00byte", "nul-byte"),
    ("a\nb", "newline"),
]


def fingerprint(root: Path) -> dict[str, str]:
    assert root.is_dir(), f"{root} is not a directory"
    digests = {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }
    assert digests, "empty tree; comparison would be vacuous"
    return digests


def make(store: SkillStore, **overrides: Any) -> Any:
    kwargs: dict[str, Any] = {
        "name": "Target Skill",
        "scope": "project",
        "purpose": "p",
        "applies_when": ["a"],
        "do_not_apply_when": ["b"],
        "activation_rules": ACTIVATION,
        "created_from": ["manual:test"],
    }
    kwargs.update(overrides)
    return store.create(**kwargs)


# --- the grammar accepts everything that exists today ----------------------


@pytest.mark.parametrize("slug", EXISTING_SLUGS)
def test_every_existing_slug_remains_valid(slug: str) -> None:
    """Backward compatibility is the whole reason this grammar was chosen."""
    assert is_canonical_slug(slug)
    validate_slug(slug)


@pytest.mark.parametrize(
    "name",
    [
        "Target Skill",
        "Two Method Escalation",
        "naïve café",
        "A/B test",
        "  spaced  ",
        "UPPER CASE",
        "with_underscores",
        "dots.and.dots",
    ],
)
def test_slugify_output_is_always_canonical(name: str) -> None:
    """The generator and the validator must agree, or normal use would break."""
    assert is_canonical_slug(slugify(name))


def test_slugify_output_is_unchanged_by_this_slice() -> None:
    assert slugify("Two Method Escalation") == "two-method-escalation"
    assert slugify("naïve café") == "naive-cafe"


# --- the grammar rejects every malformed form ------------------------------


@pytest.mark.parametrize(("slug", "label"), MALFORMED, ids=[label for _, label in MALFORMED])
def test_malformed_slugs_are_rejected(slug: str, label: str) -> None:
    assert not is_canonical_slug(slug)
    with pytest.raises(SkillKernelError):
        validate_slug(slug)


def test_a_valid_slug_cannot_represent_more_than_one_component() -> None:
    """The structural property that makes traversal impossible."""
    for slug in EXISTING_SLUGS:
        assert "/" not in slug
        assert "\\" not in slug
        assert Path(slug).name == slug


def test_the_rejection_explains_the_grammar() -> None:
    with pytest.raises(SkillKernelError, match="a-z0-9"):
        validate_slug("../../evil")


# --- Layout refuses to construct a dangerous path --------------------------


@pytest.mark.parametrize("slug", ["../../evil", "..", "a/b", "x/../y"])
def test_layout_refuses_to_build_a_non_canonical_path(layout: Layout, slug: str) -> None:
    with pytest.raises(SkillKernelError):
        layout.relative_skill_path("project", slug)
    with pytest.raises(SkillKernelError):
        layout.skill_path("project", slug)


# --- creation refuses, writing nothing and burning nothing -----------------


@pytest.mark.parametrize("slug", ["../../evil", "..", "a/b", "x/../y", ".hidden", "UPPER"])
def test_creating_with_a_malformed_slug_writes_nothing(
    layout: Layout, skills: SkillStore, slug: str
) -> None:
    make(skills, name="Anchor")  # so the tree is non-empty and the check is real
    before = fingerprint(layout.root)

    with pytest.raises(SkillKernelError):
        make(skills, name="Evil", slug=slug)

    assert fingerprint(layout.root) == before


@pytest.mark.parametrize("slug", ["../../evil", "..", "a/b"])
def test_a_malformed_slug_does_not_burn_an_identifier(
    layout: Layout, skills: SkillStore, slug: str
) -> None:
    """Validation must precede allocate_id(), which is itself a write."""
    before = load_yaml_file(skills.registry.index_file)["next_sequence"]

    with pytest.raises(SkillKernelError):
        make(skills, name="Evil", slug=slug)

    assert load_yaml_file(skills.registry.index_file)["next_sequence"] == before


def test_no_skill_directory_escapes_the_skills_tree(layout: Layout, skills: SkillStore) -> None:
    with pytest.raises(SkillKernelError):
        make(skills, name="Evil", slug="../../evil")
    assert not (layout.root / "evil").exists()


def test_a_valid_explicit_slug_still_works(layout: Layout, skills: SkillStore) -> None:
    skill = make(skills, slug="explicit-slug")
    assert skill.slug == "explicit-slug"
    assert skills.registry.path_of(skill.id) == layout.skill_path("project", "explicit-slug")


# --- doctor detects legacy corruption --------------------------------------


def test_doctor_detects_a_legacy_malformed_registered_path(
    layout: Layout, skills: SkillStore
) -> None:
    """A workspace corrupted before this grammar existed.

    Constructed by hand-editing the registry index, because the public API can
    no longer produce it. The registry entry alone is sufficient evidence; no
    scanning outside the managed tree, and nothing is repaired.
    """
    skill = make(skills)
    document = load_yaml_file(skills.registry.index_file)
    for entry in document["entries"]:
        if entry["id"] == skill.id:
            entry["path"] = "project/../../evil/skill.yaml"
    write_yaml_file(skills.registry.index_file, document)

    report = run_doctor(Layout(root=layout.root))
    assert any(f.code == "skill-location" for f in report.errors), [str(f) for f in report.errors]


def test_doctor_stays_read_only_on_a_legacy_malformed_path(
    layout: Layout, skills: SkillStore
) -> None:
    skill = make(skills)
    document = load_yaml_file(skills.registry.index_file)
    for entry in document["entries"]:
        if entry["id"] == skill.id:
            entry["path"] = "project/../../evil/skill.yaml"
    write_yaml_file(skills.registry.index_file, document)
    before = fingerprint(layout.root)

    run_doctor(Layout(root=layout.root))

    assert fingerprint(layout.root) == before
