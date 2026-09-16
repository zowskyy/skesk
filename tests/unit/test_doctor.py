"""The doctor aggregator, and the boundary that keeps two failures apart.

The interesting tests here are not "does doctor find a broken thing" — the
underlying validators already have their own suites. They are about the
distinction DEC-0010 exists to protect: a validation ERROR means the kernel
looked and found a problem; an internal error means the kernel failed to look,
so the report is incomplete and must not be read as a clean bill of health.
"""

from __future__ import annotations

from typing import Any

import pytest
from skillkernel.core.errors import IntegrityError, ValidationError
from skillkernel.core.paths import Layout
from skillkernel.core.yamlio import load_yaml_file, write_yaml_file
from skillkernel.evidence.ledger import EvidenceLedger
from skillkernel.knowledge.store import KnowledgeStore
from skillkernel.skills.store import SkillStore
from skillkernel.validation import doctor as doctor_module
from skillkernel.validation.doctor import ERROR, WARNING, DoctorReport, Finding, run_doctor


def add_evidence(layout: Layout) -> Any:
    return EvidenceLedger(layout).record(
        kind="command_output",
        summary="a run",
        project="fixture-project",
        source_type="command",
        source_detail="pytest",
        artifact_bytes=b"original output",
        artifact_name="out.txt",
    )


# --- healthy baseline ------------------------------------------------------


def test_a_fresh_workspace_is_healthy(layout: Layout) -> None:
    report = run_doctor(layout)
    assert report.errors == []
    assert report.internal_errors == []
    assert report.is_complete
    assert not report.has_errors


def test_the_json_projection_is_deterministic(layout: Layout) -> None:
    assert run_doctor(layout).to_document() == run_doctor(layout).to_document()


def test_the_json_projection_carries_no_absolute_paths(layout: Layout) -> None:
    """Absolute paths would make reports machine-specific and non-comparable."""
    add_evidence(layout)
    import json

    rendered = json.dumps(run_doctor(layout).to_document())
    assert str(layout.root) not in rendered


def test_findings_are_sorted_deterministically() -> None:
    report = DoctorReport()
    report.add(WARNING, "b-code", "loc", "w")
    report.add(ERROR, "z-code", "loc", "e2")
    report.add(ERROR, "a-code", "loc", "e1")
    assert [f.code for f in report.sorted_findings()] == ["a-code", "z-code", "b-code"]


# --- real breakages reach the report ---------------------------------------


def test_a_tampered_artifact_becomes_an_error(layout: Layout) -> None:
    record = add_evidence(layout)
    assert record.artifact is not None
    (layout.root / str(record.artifact["path"])).write_text("tampered")

    report = run_doctor(layout)
    assert report.has_errors
    assert any("does not match its recorded hash" in f.message for f in report.errors)
    assert report.is_complete, "a detected breakage is a finding, not an internal failure"


def test_an_orphan_record_file_is_a_warning_not_an_error(layout: Layout) -> None:
    """Severity matters: an orphan is recoverable, so it must not fail the gate."""
    write_yaml_file(layout.knowledge_dir / "records" / "K-9999.yaml", {"id": "K-9999"})
    report = run_doctor(layout)
    assert any(f.severity == WARNING for f in report.warnings)
    assert not report.has_errors


def test_broken_knowledge_lineage_becomes_an_error(layout: Layout) -> None:
    store = KnowledgeStore(layout)
    record = store.add(statement="A claim.", scope="universal", source_type="research")
    path = store.registry.path_of(record.id)
    document = load_yaml_file(path)
    document["status"] = "superseded"
    document["superseded_by"] = "K-9999"
    write_yaml_file(path, document)

    report = run_doctor(layout)
    assert any("K-9999" in f.message for f in report.errors)


def test_a_corrupted_skill_history_becomes_an_error(layout: Layout) -> None:
    skills = SkillStore(layout)
    skill = skills.create(
        name="Sample",
        scope="project",
        purpose="p",
        applies_when=["a"],
        do_not_apply_when=["b"],
        activation_rules={"require_any": ["x"], "require_all": [], "exclude_any": []},
        created_from=["manual:test"],
    )
    path = skills.registry.path_of(skill.id)
    document = load_yaml_file(path)
    document["classification"]["maturity"] = "trusted"
    write_yaml_file(path, document)

    report = run_doctor(layout)
    assert any("without a recorded transition" in f.message for f in report.errors)


def test_a_missing_config_is_reported(layout: Layout) -> None:
    layout.config_file.unlink()
    report = run_doctor(layout)
    assert report.has_errors
    assert report.is_complete


# --- the exception boundary (DEC-0010) -------------------------------------


def test_an_unexpected_exception_becomes_an_internal_error_not_a_finding(
    layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The distinction the whole doctrine exists to protect."""

    def explode(report: DoctorReport, target: Layout) -> None:
        raise RuntimeError("the validator itself is broken")

    monkeypatch.setattr(doctor_module, "_check_evidence", explode)
    report = run_doctor(layout)

    assert not report.is_complete
    assert len(report.internal_errors) == 1
    failure = report.internal_errors[0]
    assert failure.code == "internal_error"
    assert "evidence-ledger" in failure.location
    assert "RuntimeError" in failure.message

    # Crucially, it is NOT among the ordinary findings.
    assert all(f.code != "internal_error" for f in report.findings)
    assert not report.has_errors


@pytest.mark.parametrize("error_type", [ValidationError, IntegrityError])
def test_an_expected_domain_error_becomes_an_ordinary_finding(
    layout: Layout, monkeypatch: pytest.MonkeyPatch, error_type: type[Exception]
) -> None:
    """A SkillKernelError means the kernel worked and the repository did not."""

    def raise_domain_error(report: DoctorReport, target: Layout) -> None:
        raise error_type("something is wrong with the repository")

    monkeypatch.setattr(doctor_module, "_check_experiments", raise_domain_error)
    report = run_doctor(layout)

    assert report.has_errors
    assert report.is_complete, "a domain error is a finding; the report is still trustworthy"
    assert report.internal_errors == []


def test_one_broken_validator_does_not_stop_the_others(
    layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash in one check must not silently skip the rest."""

    def explode(report: DoctorReport, target: Layout) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(doctor_module, "_check_evidence", explode)
    monkeypatch.setattr(doctor_module, "_check_knowledge", explode)
    report = run_doctor(layout)

    assert len(report.internal_errors) == 2
    locations = {failure.location for failure in report.internal_errors}
    assert locations == {"evidence-ledger", "knowledge-lineage"}


def test_keyboard_interrupt_is_not_swallowed(
    layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BaseException subclasses must keep their normal semantics."""

    def interrupt(report: DoctorReport, target: Layout) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(doctor_module, "_check_evidence", interrupt)
    with pytest.raises(KeyboardInterrupt):
        run_doctor(layout)


def test_system_exit_is_not_swallowed(layout: Layout, monkeypatch: pytest.MonkeyPatch) -> None:
    def bail(report: DoctorReport, target: Layout) -> None:
        raise SystemExit(3)

    monkeypatch.setattr(doctor_module, "_check_skills", bail)
    with pytest.raises(SystemExit):
        run_doctor(layout)


def test_an_incomplete_report_is_never_treated_as_healthy(
    layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zero findings plus a crashed validator means 'unknown', not 'healthy'."""

    def explode(report: DoctorReport, target: Layout) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(doctor_module, "_check_evidence", explode)
    report = run_doctor(layout)

    assert not report.has_errors  # no findings...
    assert not report.is_complete  # ...but the state is unknown
    assert report.to_document()["complete"] is False


def test_the_report_counts_internal_errors_separately(
    layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(report: DoctorReport, target: Layout) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(doctor_module, "_check_evidence", explode)
    counts = run_doctor(layout).to_document()["counts"]
    assert counts["internal_error"] == 1
    assert counts["error"] == 0


# --- finding rendering -----------------------------------------------------


def test_a_finding_renders_its_location() -> None:
    finding = Finding(ERROR, "code", "EV-0001", "something went wrong")
    assert "EV-0001" in str(finding)
    assert "ERROR" in str(finding)


def test_a_finding_without_a_location_renders_cleanly() -> None:
    assert str(Finding(ERROR, "code", "", "message")) == "ERROR: message"
