"""An evaluation case id is a path component, and must behave like one.

Found by adversarial review *after* the VS4 freeze at ``a402432``. DEC-0013
closed one way for external content to choose a filesystem location -- the skill
slug. ``case_id`` was a second, unguarded way: the evaluation writer joined it
straight onto a directory it had already containment-checked, so the *parent*
was guarded and the *child* was not.

Reproduced at the frozen commit through the ordinary install API: an absolute
``case_id`` wrote outside the workspace and **overwrote a pre-existing file**.

Two independent layers are asserted, because either alone would be a single
point of failure:

* **A -- identifier validity.** A case id is one canonical path component,
  refused before any persistent write and before an identifier is allocated.
* **B -- destination containment.** Even given a valid identifier, the final
  constructed path must still land inside the examples directory that owns it.

Validation lives on the *write* path only. ``load_evaluation_suite`` discovers
cases by globbing the examples directory and never joins ``case_id`` into a
path, so a workspace holding a legacy non-canonical case id still reads.
"""

from __future__ import annotations

import dataclasses
import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from skillkernel.bundles.catalog import load_bundle
from skillkernel.bundles.installer import install_bundle, preflight
from skillkernel.core.errors import SkillKernelError, UnsafeOperationError
from skillkernel.core.paths import Layout
from skillkernel.evaluation.suite import write_evaluation_suite
from skillkernel.project.bootstrap import initialize
from skillkernel.skills.store import SkillStore
from skillkernel.validation.doctor import run_doctor

# Grouped by mechanism, not by literal string: the point is to prove a grammar,
# not to maintain a blacklist of known attack strings.
ESCAPING_IDS = [
    "../outside",
    "../../outside",
    "../../../../../../outside",
    "/absolute/path",
    "/tmp/escaped-case",
    "sub/dir/nested",
    "a\\b",
    "..\\..\\outside",
    "C:\\Windows\\escape",
    "C:relative",
    "\\\\server\\share\\escape",
    ".",
    "..",
    "...",
    "./relative",
    "/",
    "trailing/",
    "valid-prefix/../../escape",
]

# Refused for *shape* rather than for danger: a case id is a canonical component.
MALFORMED_IDS = ["", "   ", "Upper-Case", "under_score", "-leading", "trailing-", "a--b", "café"]

# Grammar-valid but unwritable. Found in the red-team pass: at 300 characters the
# filesystem raised a bare OSError *after* the skill record and history had been
# written, turning a refusal into partial state. A component with no length bound
# is not a usable path component.
OVERLONG_IDS = ["a" * 129, "a" * 300, ("ab-" * 100) + "x"]


def fingerprint(root: Path) -> dict[str, str]:
    """Hash every file under ``root``.

    Refuses an empty or missing tree: ``rglob`` yields nothing for a directory
    that does not exist, so without this guard a before/after comparison would
    compare ``{} == {}`` and prove nothing.
    """
    assert root.is_dir(), f"{root} is not a directory; nothing to fingerprint"
    digests = {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
    assert digests, f"{root} contains no files; a comparison would be vacuous"
    return digests


MANIFEST: dict[str, Any] = {
    "schema_version": 1,
    "bundle_id": "boundary-demo",
    "bundle_version": "1.0.0",
    "content_hash": None,
    "name": "Boundary demo",
    "slug": "boundary-demo",
    "scope": "core",
    "purpose": "Exercise the case-id boundary.",
    "applies_when": ["a boundary is under test"],
    "do_not_apply_when": ["nothing is under test"],
    "activation_rules": {"require_any": ["demo"], "require_all": [], "exclude_any": ["live"]},
    "inputs": ["a request"],
    "preconditions": ["the workspace is initialized"],
    "procedure": ["do the thing"],
    "success_conditions": ["the thing happened"],
    "failure_modes": ["the thing did not happen"],
    "verification": ["look at the result"],
    "evaluation": {
        "corpus_id": "boundary-corpus",
        "pass_threshold": 0.5,
        "max_false_activation_rate": 0.5,
        "description": None,
    },
}


@pytest.fixture
def build_bundle(tmp_path: Path) -> Callable[..., Path]:
    """Write a bundle whose *case_id field* carries the payload.

    The case **filename** is always innocuous. That matters: the escape has to
    ride in the field, exactly as it would in a real packaged bundle, not in a
    filename a packager would have noticed.
    """

    def build(positive_id: str) -> Path:
        root = tmp_path / "bundles"
        directory = root / "boundary-demo"
        (directory / "examples" / "positive").mkdir(parents=True, exist_ok=True)
        (directory / "examples" / "negative").mkdir(parents=True, exist_ok=True)
        (directory / "bundle.yaml").write_text(yaml.safe_dump(MANIFEST), encoding="utf-8")
        (directory / "examples" / "positive" / "harmless-filename.yaml").write_text(
            yaml.safe_dump(
                {
                    "schema_version": 1,
                    "case_id": positive_id,
                    "signals": ["demo"],
                    "description": None,
                }
            ),
            encoding="utf-8",
        )
        (directory / "examples" / "negative" / "b.yaml").write_text(
            yaml.safe_dump(
                {
                    "schema_version": 1,
                    "case_id": "ordinary-negative",
                    "signals": ["live"],
                    "description": None,
                }
            ),
            encoding="utf-8",
        )
        return root

    return build


# --- the legitimate case still works ---------------------------------------


def test_a_canonical_case_id_still_installs(
    layout: Layout, build_bundle: Callable[..., Path]
) -> None:
    """The repair must not narrow what legitimately worked."""
    bundle = load_bundle("boundary-demo", root=build_bundle("blocked-after-two-methods"))
    record = install_bundle(layout, bundle).record
    written = layout.root / "skills" / "core" / "boundary-demo" / "examples" / "positive"
    assert (written / "blocked-after-two-methods.yaml").is_file()
    assert record.slug == "boundary-demo"


# --- layer A: refused, and refusal costs nothing ---------------------------


@pytest.mark.parametrize("case_id", ESCAPING_IDS + MALFORMED_IDS + OVERLONG_IDS)
def test_a_non_canonical_case_id_is_refused_without_writing(
    layout: Layout, build_bundle: Callable[..., Path], case_id: str
) -> None:
    """Refused through a domain-error channel, leaving the workspace untouched."""
    root = build_bundle(case_id)
    before = fingerprint(layout.root)
    store = SkillStore(layout)
    sequence_before = store.registry.load_index().next_sequence

    with pytest.raises(SkillKernelError):
        install_bundle(layout, load_bundle("boundary-demo", root=root))

    assert fingerprint(layout.root) == before, "a refused install wrote to the workspace"
    assert SkillStore(layout).ids() == [], "a refused install registered a skill"
    assert SkillStore(layout).registry.load_index().next_sequence == sequence_before, (
        "a refused install burned an identifier; validation must precede allocate_id()"
    )
    assert not (layout.root / "skills" / "core" / "boundary-demo").exists()


@pytest.mark.parametrize("case_id", ESCAPING_IDS)
def test_nothing_is_written_anywhere_under_the_temp_root(
    tmp_path: Path, frozen_now: str, build_bundle: Callable[..., Path], case_id: str
) -> None:
    """Watching only the workspace would miss the escapes that matter.

    The frozen defect wrote *above* the workspace, so this fingerprints the whole
    temporary tree, which is where an escaped write actually lands.
    """
    root = build_bundle(case_id)
    layout = initialize(tmp_path / "workspace", project_name="boundary", now=frozen_now)
    before = fingerprint(tmp_path)

    with pytest.raises(SkillKernelError):
        install_bundle(layout, load_bundle("boundary-demo", root=root))

    assert fingerprint(tmp_path) == before, "a refused install escaped the workspace"


@pytest.mark.parametrize("case_id", ["../../escape", "/absolute", "sub/dir", "."])
def test_preflight_refuses_a_forged_bundle_before_any_mutation(
    layout: Layout, build_bundle: Callable[..., Path], case_id: str
) -> None:
    """Defence in depth: preflight must not rely on the catalog having checked.

    The catalog now refuses such a bundle at load time, so this forges one in
    memory -- the only way a ``Bundle`` carrying an escaping case id can still
    reach the installer. Preflight is the zero-write contract point, so it owns
    this refusal regardless of how the caller obtained the bundle.
    """
    bundle = load_bundle("boundary-demo", root=build_bundle("ordinary-positive"))
    forged = dataclasses.replace(
        bundle,
        positive_cases=({**bundle.positive_cases[0], "case_id": case_id},),
    )
    before = fingerprint(layout.root)
    sequence_before = SkillStore(layout).registry.load_index().next_sequence

    with pytest.raises(SkillKernelError):
        preflight(layout, forged)
    assert fingerprint(layout.root) == before

    with pytest.raises(SkillKernelError):
        install_bundle(layout, forged)
    assert fingerprint(layout.root) == before, "a forged bundle wrote to the workspace"
    assert SkillStore(layout).registry.load_index().next_sequence == sequence_before, (
        "a forged bundle burned an identifier"
    )


# --- the overwrite sentinel ------------------------------------------------


def test_a_pre_existing_file_outside_the_skill_is_never_overwritten(
    tmp_path: Path, frozen_now: str, build_bundle: Callable[..., Path]
) -> None:
    """At ``a402432`` this file was clobbered by an absolute ``case_id``."""
    sentinel = tmp_path / "precious.yaml"
    sentinel.write_text("important: do-not-clobber\n", encoding="utf-8")
    original = sentinel.read_bytes()

    root = build_bundle(str(tmp_path / "precious"))
    layout = initialize(tmp_path / "workspace", project_name="boundary", now=frozen_now)

    with pytest.raises(SkillKernelError):
        install_bundle(layout, load_bundle("boundary-demo", root=root))

    assert sentinel.read_bytes() == original, "the installer overwrote a file outside the workspace"


# --- doctor stays clean ----------------------------------------------------


def test_doctor_is_clean_after_a_refused_install(
    layout: Layout, build_bundle: Callable[..., Path]
) -> None:
    root = build_bundle("../../escape")
    with pytest.raises(SkillKernelError):
        install_bundle(layout, load_bundle("boundary-demo", root=root))
    report = run_doctor(layout)
    assert report.is_complete
    assert not report.has_errors, [str(f) for f in report.sorted_findings()]


# --- the catalog refuses it, so such a bundle never even loads -------------


@pytest.mark.parametrize("case_id", ["../../escape", "/absolute", ".", "sub/dir"])
def test_the_catalog_refuses_to_load_such_a_bundle(
    build_bundle: Callable[..., Path], case_id: str
) -> None:
    with pytest.raises(SkillKernelError):
        load_bundle("boundary-demo", root=build_bundle(case_id))


# --- layer B: the reusable writer defends its own boundary ----------------


@pytest.mark.parametrize("case_id", ["../../escape", "/absolute/path", "sub/dir"])
def test_the_evaluation_writer_cannot_be_made_to_escape(layout: Layout, case_id: str) -> None:
    """The installer must not be the only guard on a reusable public API."""
    skill = SkillStore(layout).create(name="Writer boundary", scope="project")
    before = fingerprint(layout.root)

    with pytest.raises(SkillKernelError):
        write_evaluation_suite(
            layout,
            skill.id,
            corpus_id="writer-corpus",
            pass_threshold=0.5,
            max_false_activation_rate=0.5,
            positive=[{"case_id": case_id, "signals": ["x"]}],
            negative=[{"case_id": "ordinary-negative", "signals": ["y"]}],
        )

    assert fingerprint(layout.root) == before, "the evaluation writer escaped its suite"


def test_containment_is_checked_independently_of_the_grammar(layout: Layout) -> None:
    """Layer B must hold even if layer A were bypassed.

    ``require_inside`` proves only that a path is in the workspace; the frozen
    defect existed precisely because that was checked on the parent before an
    unsafe child was appended.
    """
    skill = SkillStore(layout).create(name="Independent guard", scope="project")
    owner = SkillStore(layout).skill_dir(skill.id)

    # Inside the workspace, yet outside the owning directory: require_inside
    # accepts it, so containment needs its own expression.
    escaped = owner / ".." / ".." / "escape.yaml"
    layout.require_inside(escaped)
    with pytest.raises(UnsafeOperationError):
        layout.require_within(owner, escaped)

    # And a legitimate child is still accepted.
    assert layout.require_within(owner, owner / "examples" / "positive" / "ok.yaml")


def test_the_longest_legitimate_case_id_is_still_accepted() -> None:
    """The bound must not narrow anything real: the longest shipped id is 34 chars."""
    from skillkernel.core.paths import MAX_COMPONENT_LENGTH, validate_case_id

    assert validate_case_id("blocked-but-first-attempt-underway")
    assert validate_case_id("a" * MAX_COMPONENT_LENGTH)
