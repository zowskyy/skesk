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
from typing import Any

from skillkernel.core.config import load_config
from skillkernel.core.errors import SkillKernelError
from skillkernel.core.paths import Layout
from skillkernel.discovery.observations import ObservationStore
from skillkernel.evidence.ledger import EvidenceLedger
from skillkernel.experiments.store import ExperimentStore
from skillkernel.knowledge.store import KnowledgeStore
from skillkernel.project.profile import load_profile
from skillkernel.registry import Registry
from skillkernel.skills.history import history_issues
from skillkernel.skills.store import SkillStore, skills_registry
from skillkernel.validation.provenance import verify_provenance

__all__ = ["DoctorReport", "Finding", "run_doctor"]

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
        self.findings.append(Finding(severity, code, location, message))

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
    report = DoctorReport()

    def check(name: str) -> Callable[[Callable[[], None]], None]:
        return _guard(report, name)

    # --- configuration and profile -----------------------------------------
    check("config")(lambda: _check_config(layout))
    check("profile")(lambda: _check_profile(layout))

    # --- registries ---------------------------------------------------------
    for name, registry in _domain_registries(layout):
        check(f"registry:{name}")(lambda r=registry, n=name: _check_registry(report, n, r))  # type: ignore[misc]

    # --- domain integrity ---------------------------------------------------
    check("evidence-ledger")(lambda: _check_evidence(report, layout))
    check("experiments")(lambda: _check_experiments(report, layout))
    check("knowledge-lineage")(lambda: _check_knowledge(report, layout))
    check("skills")(lambda: _check_skills(report, layout))

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

    for orphan in registry.orphan_record_files():
        report.add(
            WARNING,
            f"registry:{name}",
            layout_relative(registry.layout, orphan),
            "record file is not referenced by the registry index",
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
    for skill_id in store.ids():
        skill = store.get(skill_id)
        history = store.history(skill_id)
        for issue in history_issues(history, skill_id=skill_id, current_maturity=skill.maturity):
            report.add(ERROR, "skill-history", skill_id, issue)
        for issue in verify_provenance(layout, skill_id).findings:
            report.add(ERROR, "skill-provenance", skill_id, issue)
