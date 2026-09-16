"""Repository health check.

``doctor`` is an *aggregator*. It owns no validation rules of its own: every
check below delegates to a function that already exists and is already tested
elsewhere. That is deliberate — a second implementation of a rule is a second
place for it to drift.

The exception boundary is the part worth reading carefully. Two things that look
similar are kept rigorously apart (DEC-0010):

``SkillKernelError``
    An *expected* domain outcome. The kernel ran its checks and something was
    wrong with the repository. Becomes an ``ERROR`` finding; the report is
    trustworthy.

any other ``Exception``
    An *implementation fault*. The kernel itself broke while checking, so an
    unknown number of checks never ran. Becomes an ``internal_error``, and the
    caller must not read the report as a completed validation.

Collapsing those would fail in the dangerous direction: a repository whose true
state is *unknown* would be reported as merely *unhealthy*.

``BaseException`` is deliberately **not** caught, so ``KeyboardInterrupt`` and
``SystemExit`` keep their normal semantics.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any

from skillkernel.bundles.model import SOURCE_FIELD, validate_x_source
from skillkernel.core.config import load_config
from skillkernel.core.errors import SkillKernelError
from skillkernel.core.paths import Layout
from skillkernel.discovery.observations import ObservationStore
from skillkernel.evaluation.runner import INPUT_DIGEST_ATTRIBUTE
from skillkernel.evaluation.suite import read_evaluation_inputs
from skillkernel.evidence.ledger import EvidenceLedger
from skillkernel.experiments.store import ExperimentStore
from skillkernel.knowledge.store import KnowledgeStore
from skillkernel.project.profile import load_profile
from skillkernel.promotion.gates import (
    EVIDENCE_BACKED_MATURITIES,
    current_input_digest,
    passing_evaluations,
)
from skillkernel.registry import Registry
from skillkernel.skills.history import history_issues
from skillkernel.skills.store import SkillStore, skills_registry
from skillkernel.validation.provenance import verify_provenance

__all__ = ["DoctorReport", "Finding", "run_doctor"]

WORKSPACE_TOKEN = "<workspace>"
"""Stands in for the workspace root in machine-readable output.

Machine-readable doctor output is workspace-independent (DEC-0012): two
equivalent workspaces under different absolute roots must produce identical
reports. Lower-level exceptions keep their absolute paths, which are useful in
a traceback; normalization happens here, at the boundary that makes the claim.
"""


def normalize_workspace_paths(text: str, root: Path | None) -> str:
    """Replace a known workspace root with :data:`WORKSPACE_TOKEN`.

    Targeted at the specific root this report describes, in both its literal
    and resolved forms, rather than pattern-matching anything path-shaped.
    """
    if root is None:
        return text
    normalized = text
    for form in sorted({str(root), str(root.resolve())}, key=len, reverse=True):
        if form and form not in ("/", "\\"):
            normalized = normalized.replace(form, WORKSPACE_TOKEN)
    return normalized


ERROR = "ERROR"
WARNING = "WARNING"
INFO = "INFO"

_SEVERITY_RANK = {ERROR: 0, WARNING: 1, INFO: 2}


@dataclass(frozen=True)
class Finding:
    """One observation about the repository's health."""

    severity: str
    code: str
    location: str
    message: str

    def sort_key(self) -> tuple[int, str, str, str]:
        return (_SEVERITY_RANK.get(self.severity, 99), self.code, self.location, self.message)

    def to_document(self) -> dict[str, str]:
        return {
            "severity": self.severity,
            "code": self.code,
            "location": self.location,
            "message": self.message,
        }

    def __str__(self) -> str:
        where = f" [{self.location}]" if self.location else ""
        return f"{self.severity}: {self.message}{where}"


@dataclass
class DoctorReport:
    """The outcome of a health check.

    ``internal_errors`` is kept separate from ``findings`` on purpose. A finding
    is something the kernel learned about the repository; an internal error is
    the kernel failing to look.
    """

    findings: list[Finding] = field(default_factory=list)
    internal_errors: list[Finding] = field(default_factory=list)
    workspace_root: Path | None = None

    @property
    def errors(self) -> list[Finding]:
        return [finding for finding in self.findings if finding.severity == ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [finding for finding in self.findings if finding.severity == WARNING]

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)

    @property
    def is_complete(self) -> bool:
        """False when a validator raised, meaning some checks never ran."""
        return not self.internal_errors

    def add(self, severity: str, code: str, location: str, message: str) -> None:
        """Record a finding, normalizing any workspace-root path it carries."""
        self.findings.append(
            Finding(
                severity,
                code,
                normalize_workspace_paths(location, self.workspace_root),
                normalize_workspace_paths(message, self.workspace_root),
            )
        )

    def add_internal_error(self, check_name: str, message: str) -> None:
        self.internal_errors.append(
            Finding(
                severity=ERROR,
                code="internal_error",
                location=check_name,
                message=normalize_workspace_paths(message, self.workspace_root),
            )
        )

    def sorted_findings(self) -> list[Finding]:
        return sorted(self.findings, key=Finding.sort_key)

    def to_document(self) -> dict[str, Any]:
        """A deterministic, machine-readable projection.

        Carries no timestamps and no absolute paths, so two runs over an
        unchanged repository serialize byte-identically.
        """
        return {
            "schema_version": 1,
            "complete": self.is_complete,
            "counts": {
                "error": len(self.errors),
                "warning": len(self.warnings),
                "info": len([f for f in self.findings if f.severity == INFO]),
                "internal_error": len(self.internal_errors),
            },
            "findings": [finding.to_document() for finding in self.sorted_findings()],
            "internal_errors": [
                finding.to_document()
                for finding in sorted(self.internal_errors, key=Finding.sort_key)
            ],
        }


def _guard(report: DoctorReport, check_name: str) -> Callable[[Callable[[], None]], None]:
    """Run one check, separating domain outcomes from implementation faults."""

    def run(body: Callable[[], None]) -> None:
        try:
            body()
        except SkillKernelError as exc:
            # Expected: the kernel looked, and found something wrong.
            report.add(ERROR, check_name, "", str(exc))
        except Exception as exc:  # noqa: BLE001 - deliberate boundary; see DEC-0010
            # Unexpected: the kernel failed while looking. Not a finding.
            report.internal_errors.append(
                Finding(
                    severity=ERROR,
                    code="internal_error",
                    location=check_name,
                    message=(
                        f"the {check_name} check raised an unexpected {type(exc).__name__}: {exc}"
                    ),
                )
            )

    return run


def _domain_registries(layout: Layout) -> Iterator[tuple[str, Registry]]:
    yield "knowledge", KnowledgeStore(layout).registry
    yield "experiments", ExperimentStore(layout).registry
    yield "evidence", EvidenceLedger(layout).registry
    yield "observations", ObservationStore(layout).registry
    yield "skills", skills_registry(layout)


def run_doctor(layout: Layout) -> DoctorReport:
    """Aggregate every existing validator into one report."""
    report = DoctorReport(workspace_root=layout.root)

    def check(name: str) -> Callable[[Callable[[], None]], None]:
        return _guard(report, name)

    # --- configuration and profile -----------------------------------------
    check("config")(lambda: _check_config(layout))
    check("profile")(lambda: _check_profile(layout))

    # --- registries ---------------------------------------------------------
    for name, registry in _domain_registries(layout):
        check(f"registry:{name}")(partial(_check_registry, report, name, registry))

    # --- domain integrity ---------------------------------------------------
    check("evidence-ledger")(lambda: _check_evidence(report, layout))
    check("experiments")(lambda: _check_experiments(report, layout))
    check("knowledge-lineage")(lambda: _check_knowledge(report, layout))
    check("skills")(lambda: _check_skills(report, layout))
    check("skill-location")(lambda: _check_skill_locations(report, layout))
    check("skill-source")(lambda: _check_skill_sources(report, layout))
    check("evaluation-input")(lambda: _check_evaluation_inputs(report, layout))
    check("evaluation-cases")(lambda: _check_evaluation_cases(report, layout))

    return report


def _check_config(layout: Layout) -> None:
    load_config(layout)


def _check_profile(layout: Layout) -> None:
    load_profile(layout)


def _check_registry(report: DoctorReport, name: str, registry: Registry) -> None:
    if not registry.exists():
        report.add(
            ERROR,
            f"registry:{name}",
            layout_relative(registry.layout, registry.index_file),
            f"the {name} registry index is missing",
        )
        return

    registry.load_index()
    for record_id in registry.ids():
        try:
            registry.load(record_id)
        except SkillKernelError as exc:
            report.add(ERROR, f"registry:{name}", record_id, str(exc))

    # Each domain is asked about its own topology. doctor stays an aggregator:
    # it does not know, and must not learn, where any domain keeps its files.
    for orphan in registry.orphan_states():
        report.add(
            WARNING,
            f"registry:{name}",
            layout_relative(registry.layout, orphan),
            "managed state on disk is not referenced by the registry index",
        )


def layout_relative(layout: Layout, path: Any) -> str:
    """Repository-relative location, so reports never leak absolute paths."""
    try:
        return layout.relative(path)
    except ValueError:
        return str(path)


def _check_evidence(report: DoctorReport, layout: Layout) -> None:
    for finding in EvidenceLedger(layout).verify():
        report.add(ERROR, "evidence-ledger", finding.evidence_id, finding.message)


def _check_experiments(report: DoctorReport, layout: Layout) -> None:
    for finding in ExperimentStore(layout).verify():
        report.add(ERROR, "experiments", finding.experiment_id, finding.message)


def _check_knowledge(report: DoctorReport, layout: Layout) -> None:
    for issue in KnowledgeStore(layout).lineage_issues():
        report.add(ERROR, "knowledge-lineage", "", issue)


def _check_skills(report: DoctorReport, layout: Layout) -> None:
    store = SkillStore(layout)
    for skill_id in sorted(store.ids()):
        _guarded_skill(report, "skill-history", skill_id)(
            partial(_check_one_skill, report, store, layout, skill_id)
        )


def _check_one_skill(
    report: DoctorReport, store: SkillStore, layout: Layout, skill_id: str
) -> None:
    skill = store.get(skill_id)
    history = store.history(skill_id)
    for issue in history_issues(history, skill_id=skill_id, current_maturity=skill.maturity):
        report.add(ERROR, "skill-history", skill_id, issue)
    for issue in verify_provenance(layout, skill_id).findings:
        report.add(ERROR, "skill-provenance", skill_id, issue)


def _guarded_skill(
    report: DoctorReport, code: str, skill_id: str
) -> Callable[[Callable[[], None]], None]:
    """Isolate the inspection of one persisted skill.

    ``_guard`` already separates *the kernel found a problem* from *the kernel
    broke while looking*, but it wraps a whole check. That granularity was too
    coarse: one damaged record raised out of the loop, so every later skill went
    uninspected and the finding arrived attributed to the check rather than to
    the skill. Both skill loops promised per-skill isolation in their comments
    and neither delivered it.

    The boundary here is exactly one skill. A domain error becomes that skill's
    own finding and iteration continues; anything else is still re-raised, so
    ``_guard`` can record it as an internal error and mark the report incomplete.
    Narrowing over-broad abortion must not erase that distinction (DEC-0010).
    """

    def run(body: Callable[[], None]) -> None:
        try:
            body()
        except SkillKernelError as exc:
            report.add(ERROR, code, skill_id, str(exc))

    return run


def _check_skill_locations(report: DoctorReport, layout: Layout) -> None:
    """Detect a skill whose declared identity disagrees with where it is stored.

    A safety net for workspaces corrupted before this check existed, or by a
    stray editor. Enforcement lives at the persistence boundary in
    :class:`~skillkernel.skills.store.SkillStore`; this only reports, and never
    repairs, moves or normalizes anything.
    """
    store = SkillStore(layout)
    index = store.registry.load_index()

    # Duplicate ownership is derived from the index alone, before any record is
    # read. A corrupted record must not stop this from being reported -- and
    # when two skills share a directory, at least one of them is unreadable by
    # construction, so a load-first ordering would hide exactly the case that
    # matters most.
    owners: dict[str, list[str]] = {}
    for skill_id, entry in index.entries.items():
        owners.setdefault(entry.path, []).append(skill_id)
    for path, ids in sorted(owners.items()):
        if len(ids) > 1:
            report.add(
                ERROR,
                "skill-location",
                path,
                f"is registered to more than one skill ({', '.join(sorted(ids))}); "
                "only one skill may own a location, and the others' records and "
                "history have almost certainly been overwritten.",
            )

    # A registered path that is not structurally canonical is corruption the
    # index alone proves. It is checked before any record is read, because such
    # a path usually makes its own record unreadable -- and a load-first
    # ordering would skip exactly the entries that are worst. Nothing outside
    # the managed tree is scanned, and nothing is repaired.
    for skill_id in sorted(index.entries):
        entry = index.entries[skill_id]
        if layout.parse_relative_skill_path(entry.path) is None:
            report.add(
                ERROR,
                "skill-location",
                skill_id,
                f"is registered at {entry.path}, which is not a canonical skill "
                "location. A canonical location is <scope>/<slug>/skill.yaml with a "
                "well-formed slug; this entry could place the record outside the "
                "skills tree.",
            )

    # Per-skill checks are isolated: an unreadable record is already reported by
    # the registry check, and must not abort the remaining skills.
    for skill_id in sorted(index.entries):
        entry = index.entries[skill_id]
        try:
            skill = store.get(skill_id)
        except SkillKernelError:
            continue
        # Inside the per-skill boundary on purpose. A record can load cleanly and
        # still declare a slug that is not a canonical component -- legacy state,
        # or a hand edit -- and computing its canonical path raises. Left outside,
        # that raise aborted the loop and silently took every later skill's
        # finding with it.
        try:
            canonical = layout.relative_skill_path(skill.scope, skill.slug)
        except SkillKernelError as exc:
            report.add(ERROR, "skill-location", skill_id, str(exc))
            continue
        if entry.path != canonical:
            report.add(
                ERROR,
                "skill-location",
                skill_id,
                f"is stored at {entry.path}, but its declared scope "
                f"{skill.scope!r} and slug {skill.slug!r} place it at {canonical}. "
                "The record and its location disagree, so another skill could occupy "
                "that directory and overwrite this one.",
            )


def _check_evaluation_inputs(report: DoctorReport, layout: Layout) -> None:
    """Report a skill whose passing evaluations can no longer name their inputs.

    The unit is the *skill*, not the record. A stale or legacy evaluation is not
    itself a defect -- it is immutable history, and history is allowed to
    describe inputs that have since changed. What matters is whether anything
    current stands beside it. So a skill with a current passing evaluation is
    clean however many superseded records it also holds, and re-evaluating is
    what clears the finding rather than editing or deleting anything.
    """
    store = SkillStore(layout)
    for skill_id in sorted(store.ids()):
        _guarded_skill(report, "evaluation-input", skill_id)(
            partial(_check_one_evaluation_input, report, store, layout, skill_id)
        )


def _check_one_evaluation_input(
    report: DoctorReport, store: SkillStore, layout: Layout, skill_id: str
) -> None:
    skill = store.get(skill_id)
    historical = passing_evaluations(layout, skill, current_only=False)
    if not historical:
        # A skill that has never been evaluated is not in an inconsistent state.
        return
    if passing_evaluations(layout, skill):
        return

    digest = current_input_digest(layout, skill_id)
    if digest is None:
        code = "evaluation-input:unreadable"
        message = (
            "has passing evaluation evidence, but its evaluation inputs cannot be read, so "
            "no evaluation can be shown to describe them. Repair the suite and re-evaluate."
        )
    elif any(record.attributes.get(INPUT_DIGEST_ATTRIBUTE) for record in historical):
        code = "evaluation-input:mismatch"
        message = (
            "has no passing evaluation matching the evaluation inputs now on disk; the "
            "corpus or its configuration changed after the evaluation ran. Re-evaluate to "
            "restore current evidence. The earlier evidence stays as history."
        )
    else:
        code = "evaluation-input:legacy"
        message = (
            "has passing evaluation evidence recorded before evaluation inputs were "
            "identified, so it cannot establish which inputs produced it. Re-evaluate to "
            "restore current evidence. The earlier evidence stays as history."
        )

    # Severity follows consequence, not age: a promoted skill's standing rests on
    # this evidence, while a younger skill is merely being told to re-run first.
    severity = ERROR if skill.maturity in EVIDENCE_BACKED_MATURITIES else WARNING
    report.add(severity, code, skill_id, message)


CASE_SHAPED_SUFFIXES = (".yaml", ".yml")
"""What counts as case-shaped content inside a skill's ``examples/`` tree.

``.yml`` is included precisely because it is *not* a case file: a suite names
``.yaml`` and nothing else, so a case saved under the other spelling is silently
never scored. That near-miss is the reason to look at all. Everything else --
notes, drafts, diagrams, subdirectories -- is authoring material and is left
alone. ``doctor`` is not a filesystem linter.
"""


def _check_evaluation_cases(report: DoctorReport, layout: Layout) -> None:
    """Report case-shaped content that no evaluation definition names.

    Under manifest authority an unnamed file is simply not read, so it cannot
    corrupt an evaluation, change a digest or affect a promotion. That is why
    this is a ``WARNING`` at every maturity and never an ``ERROR``: the finding
    is about a person's expectation, not the repository's integrity. A case
    someone believes is being scored, and is not, is a silent gap in coverage --
    and after an interrupted authoring pass, the retired-but-still-present files
    are exactly what this surfaces.
    """
    store = SkillStore(layout)
    for skill_id in sorted(store.ids()):
        _guarded_skill(report, "evaluation-cases", skill_id)(
            partial(_check_one_skill_cases, report, store, layout, skill_id)
        )


def _check_one_skill_cases(
    report: DoctorReport, store: SkillStore, layout: Layout, skill_id: str
) -> None:
    try:
        inputs = read_evaluation_inputs(layout, skill_id)
    except SkillKernelError:
        # No readable definition means no manifest, and so no answer to "which
        # files belong here". ``evaluation-input`` owns readability; reporting
        # every file as unnamed on the strength of a broken definition would be
        # guessing, and would bury the real finding under the guesses.
        return

    examples = layout.require_inside(store.skill_dir(skill_id) / "examples")
    if not examples.is_dir():
        return
    comprised = {relative for relative, _document in inputs.cases}
    root = layout.root.resolve()
    for entry in sorted(examples.rglob("*")):
        if not entry.name.endswith(CASE_SHAPED_SUFFIXES):
            continue
        # The entry's own name, not whatever it points at. Resolving the leaf
        # would make a symlink indistinguishable from the case it aliases, and
        # an alias is a second entry that the definition does not name.
        relative = (entry.parent.resolve() / entry.name).relative_to(root).as_posix()
        if relative in comprised:
            continue
        report.add(
            WARNING,
            "evaluation-cases:unmanifested",
            relative,
            f"looks like an evaluation case, but {skill_id}'s evaluation definition does "
            "not name it, so it is never loaded and never scored. Author it into the "
            "suite if it should count, or remove it.",
        )


def _check_skill_sources(report: DoctorReport, layout: Layout) -> None:
    """Validate the source provenance of any skill that claims one.

    ``provenance.x_source`` is written only by the bundle installer, but the
    record is a plain file a person can edit. This is the read-side half of the
    same contract: a record claiming a source shape the installer would never
    have produced is reported, so an unaudited or hand-forged provenance claim
    cannot pass as a real one.

    A skill with no ``x_source`` is not a finding. Most skills are authored
    locally and have no external source to declare.
    """
    store = SkillStore(layout)
    # Per-skill isolation: an unreadable record is already reported by the
    # registry check, and must not stop the remaining skills from being checked.
    for skill_id in sorted(store.ids()):
        try:
            skill = store.get(skill_id)
        except SkillKernelError:
            continue
        declared = skill.provenance.get(SOURCE_FIELD)
        if declared is None:
            continue
        try:
            validate_x_source(declared, source=skill_id)
        except SkillKernelError as exc:
            report.add(ERROR, "skill-source", skill_id, str(exc))
