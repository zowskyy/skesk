"""Installing a portable bundle into a workspace.

The rule this module exists to enforce: **a bundle contributes a definition, and
nothing else.** The receiving workspace allocates the identity, starts the
maturity at ``observed`` like every other skill, and earns every piece of
evidence locally. Nothing in a bundle can shorten that path.

The second rule is about residue. Every check that can fail for a bundle- or
policy-related reason runs in :func:`preflight`, before the first byte is
written -- including a full dry run of the record the installer intends to
create. After preflight the only remaining failure mode is genuine I/O
interruption, which is exactly the failure mode every other caller of
``SkillStore.create`` already has. The installer adds no new partial-state risk
and needs no transaction machinery of its own.

Refusal is the answer to every collision. This module never updates, merges,
renames or overwrites an installed skill: those would each be a silent way for
imported content to displace content a workspace earned.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from skillkernel.bundles.catalog import load_bundle
from skillkernel.bundles.model import SOURCE_FIELD, Bundle, validate_x_source
from skillkernel.core.clock import now_iso
from skillkernel.core.errors import UnsafeOperationError, ValidationError
from skillkernel.core.ids import SKILL, format_id
from skillkernel.core.paths import Layout
from skillkernel.evaluation.suite import write_evaluation_suite
from skillkernel.skills.model import SKILL_SCOPES, SkillRecord, new_skill_document
from skillkernel.skills.store import EDITABLE_FIELDS, SkillStore

__all__ = ["InstallPlan", "InstallResult", "install_bundle", "install_bundle_by_id", "preflight"]

_DRY_RUN_ID = format_id(SKILL, 1)
"""A stand-in identifier for the pre-write dry run.

It is never allocated and never written. Every identifier has the same shape, so
validating the intended document under this one proves the real document
validates too -- and it proves it before ``allocate_id`` has burned a number.
"""


@dataclass(frozen=True)
class InstallPlan:
    """What a validated install is going to do. Produced without writing."""

    bundle: Bundle
    scope: str
    slug: str
    relative_path: str
    source: dict[str, str]
    definition: dict[str, Any]


@dataclass(frozen=True)
class InstallResult:
    record: SkillRecord
    bundle_id: str
    bundle_version: str
    content_hash: str
    relative_path: str
    positive_cases: int
    negative_cases: int


def _installed_source(record: SkillRecord) -> dict[str, Any] | None:
    """The source block of an installed skill, if it declares one."""
    value = record.provenance.get(SOURCE_FIELD)
    return value if isinstance(value, dict) else None


def preflight(layout: Layout, bundle: Bundle) -> InstallPlan:
    """Prove the install can succeed. Writes nothing, under any outcome.

    Raises the first refusal it finds, naming what is in the way. A caller that
    reaches the end of this function has been told that the only thing left
    between it and an installed skill is the filesystem.
    """
    store = SkillStore(layout)

    scope = bundle.scope
    if scope not in SKILL_SCOPES:
        raise ValidationError(f"bundle {bundle.bundle_id!r} declares unknown scope {scope!r}")

    # Raises if the slug is not a single well-formed path component (DEC-0013).
    relative_path = layout.relative_skill_path(scope, bundle.slug)

    # The definition a bundle carries must be exactly what a workspace is
    # allowed to edit. If these ever diverge, the bundle format is trying to
    # write something the kernel owns, and that is a bug in this repository
    # rather than a problem with the bundle.
    definition = bundle.definition_fields()
    kernel_owned = sorted(set(definition) - EDITABLE_FIELDS)
    if kernel_owned:
        raise ValidationError(
            f"the bundle format carries {', '.join(kernel_owned)}, which the skill store "
            "does not accept as an editable field. This is an internal inconsistency."
        )

    existing = store.find_by_slug(scope, bundle.slug)
    if existing is not None:
        raise ValidationError(
            f"a {scope} skill with slug {bundle.slug!r} already exists ({existing.id}). "
            "Installation never overwrites, merges or renames an existing skill."
        )

    source = validate_x_source(bundle.source_block(), source=f"bundle {bundle.bundle_id!r}")

    for record in store.all():
        installed = _installed_source(record)
        if installed is None:
            continue
        if installed.get("bundle_id") == bundle.bundle_id:
            raise ValidationError(
                f"bundle {bundle.bundle_id!r} is already installed as {record.id} "
                f"(version {installed.get('bundle_version')!r}). Upgrading an installed "
                "bundle is a separate, gated operation that does not exist yet."
            )
        if installed.get("content_hash") == bundle.content_hash:
            raise ValidationError(
                f"{record.id} was installed from bundle "
                f"{installed.get('bundle_id')!r} with the same content hash "
                f"{bundle.content_hash}. Installing identical content under a second "
                "bundle identity would make provenance ambiguous."
            )

    # A directory left behind by an interrupted write is not in the index, so no
    # index check can see it. Writing into it would silently adopt whatever it
    # contains, so it is refused and reported rather than reused.
    directory = layout.skill_path(scope, bundle.slug).parent
    if directory.exists():
        raise UnsafeOperationError(
            f"{relative_path.rsplit('/', 1)[0]} already exists on disk but no skill is "
            "registered there. Installing would write into an unmanaged directory. "
            "Inspect it and remove it deliberately; nothing has been written."
        )

    _dry_run(bundle, scope, definition, source)
    _check_case_ids(bundle)
    return InstallPlan(
        bundle=bundle,
        scope=scope,
        slug=bundle.slug,
        relative_path=relative_path,
        source=source,
        definition=definition,
    )


def _dry_run(
    bundle: Bundle, scope: str, definition: dict[str, Any], source: dict[str, str]
) -> None:
    """Build and validate the exact record shape the install will write."""
    timestamp = now_iso()
    document = new_skill_document(
        record_id=_DRY_RUN_ID,
        name=bundle.name,
        slug=bundle.slug,
        scope=scope,
        now=timestamp,
        created_by="bundled",
        created_from=[_created_from(bundle)],
    )
    document.update(definition)
    document["provenance"] = {**document["provenance"], SOURCE_FIELD: dict(source)}
    SkillRecord.from_document(document, source=f"bundle {bundle.bundle_id!r}")


def _check_case_ids(bundle: Bundle) -> None:
    """Refuse a duplicate case id before the suite writer would.

    ``write_evaluation_suite`` raises on a duplicate, but it runs after the
    skill exists. Checking here keeps the refusal on the zero-write side.
    """
    seen: set[str] = set()
    for case in (*bundle.positive_cases, *bundle.negative_cases):
        case_id = str(case["case_id"])
        if case_id in seen:
            raise ValidationError(
                f"bundle {bundle.bundle_id!r} uses case id {case_id!r} more than once; "
                "a case must be either a positive or a negative example, never both"
            )
        seen.add(case_id)


def _created_from(bundle: Bundle) -> str:
    return f"bundle:{bundle.bundle_id}@{bundle.bundle_version}"


def install_bundle(layout: Layout, bundle: Bundle, *, now: str | None = None) -> InstallResult:
    """Install a validated bundle as a new workspace-local skill."""
    plan = preflight(layout, bundle)
    store = SkillStore(layout)
    timestamp = now or now_iso()

    record = store.create(
        name=bundle.name,
        scope=plan.scope,
        slug=plan.slug,
        created_by="bundled",
        created_from=[_created_from(bundle)],
        now=timestamp,
    )

    # One write for the whole definition and its source provenance. Provenance
    # has no generic editor on purpose -- it is kernel-owned -- so the installer
    # sets exactly the block it validated above and nothing else, then goes back
    # through the store's ordinary save path, which still checks the location
    # invariant before anything is persisted.
    document = dict(record.raw)
    document.update(plan.definition)
    document["provenance"] = {**document["provenance"], SOURCE_FIELD: dict(plan.source)}
    document["updated_at"] = timestamp
    record = store.save(SkillRecord.from_document(document, source=record.id))

    evaluation = bundle.evaluation
    write_evaluation_suite(
        layout,
        record.id,
        corpus_id=str(evaluation["corpus_id"]),
        pass_threshold=float(evaluation["pass_threshold"]),
        max_false_activation_rate=float(evaluation["max_false_activation_rate"]),
        positive=bundle.positive_cases,
        negative=bundle.negative_cases,
        description=evaluation.get("description"),
    )

    return InstallResult(
        record=record,
        bundle_id=bundle.bundle_id,
        bundle_version=bundle.bundle_version,
        content_hash=bundle.content_hash,
        relative_path=plan.relative_path,
        positive_cases=len(bundle.positive_cases),
        negative_cases=len(bundle.negative_cases),
    )


def install_bundle_by_id(
    layout: Layout, bundle_id: str, *, now: str | None = None
) -> InstallResult:
    """Load a packaged bundle by identifier and install it."""
    return install_bundle(layout, load_bundle(bundle_id), now=now)
