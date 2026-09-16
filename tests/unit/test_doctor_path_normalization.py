"""Machine-readable doctor output is workspace-independent (DEC-0012).

This closes a defect shipped in VS2. ARCHITECTURE.md claimed
``DoctorReport.to_document()`` carries "no absolute paths, so it is byte-stable
across runs **and comparable across machines**". The byte-stability half held;
the comparability half did not. An ``IntegrityError`` raised inside
``Registry.load`` embeds an absolute path, and ``doctor`` stored that exception
text verbatim as a finding message. VS2's test only exercised a healthy
workspace, where no such message is produced.

The contract was right and the implementation was wrong, so the fix normalizes
output rather than weakening the claim. Lower-level exceptions keep their
absolute paths, which are what you want in a traceback; normalization happens at
the boundary that makes the promise.

These tests are deliberately independent of the location invariant. The two
defects were found together but are unrelated, and entangling them would make
either one harder to attribute later.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from skillkernel.core.paths import Layout
from skillkernel.core.yamlio import load_yaml_file, write_yaml_file
from skillkernel.project.bootstrap import initialize
from skillkernel.skills.store import SkillStore
from skillkernel.validation.doctor import (
    WORKSPACE_TOKEN,
    DoctorReport,
    normalize_workspace_paths,
    run_doctor,
)

ACTIVATION = {"require_any": ["trigger"], "require_all": [], "exclude_any": []}


# --- the normalizer itself -------------------------------------------------


def test_the_workspace_root_is_replaced_by_a_stable_token() -> None:
    root = Path("/some/workspace")
    text = "/some/workspace/skills/project/x/skill.yaml declares id 'SKILL-0002'"
    assert normalize_workspace_paths(text, root) == (
        f"{WORKSPACE_TOKEN}/skills/project/x/skill.yaml declares id 'SKILL-0002'"
    )


def test_normalization_is_a_no_op_without_a_root() -> None:
    assert normalize_workspace_paths("/a/b", None) == "/a/b"


def test_unrelated_absolute_paths_are_left_alone() -> None:
    """Targeted at the known root, not at anything path-shaped."""
    text = "see /usr/share/doc/readme and /etc/hosts"
    assert normalize_workspace_paths(text, Path("/some/workspace")) == text


def test_a_root_of_slash_is_not_used_for_replacement() -> None:
    """Replacing "/" would mangle every path in the message."""
    assert normalize_workspace_paths("/etc/hosts", Path("/")) == "/etc/hosts"


def test_findings_are_normalized_as_they_are_added() -> None:
    report = DoctorReport(workspace_root=Path("/w"))
    report.add("ERROR", "code", "/w/a.yaml", "/w/a.yaml is broken")
    assert report.findings[0].location == f"{WORKSPACE_TOKEN}/a.yaml"
    assert report.findings[0].message == f"{WORKSPACE_TOKEN}/a.yaml is broken"


def test_internal_errors_are_normalized_too() -> None:
    report = DoctorReport(workspace_root=Path("/w"))
    report.add_internal_error("check", "boom at /w/thing.yaml")
    assert WORKSPACE_TOKEN in report.internal_errors[0].message
    assert "/w/" not in report.internal_errors[0].message


# --- end to end, on the error path VS2's test missed -----------------------


def break_a_record(layout: Layout) -> None:
    """Produce a workspace whose diagnostics quote an absolute path."""
    store = SkillStore(layout)
    skill = store.create(
        name="Target",
        scope="project",
        purpose="p",
        applies_when=["a"],
        do_not_apply_when=["b"],
        activation_rules=ACTIVATION,
        created_from=["manual:t"],
    )
    path = store.registry.path_of(skill.id)
    document = load_yaml_file(path)
    document["id"] = "SKILL-0999"  # id/registration mismatch -> IntegrityError
    write_yaml_file(path, document)


def test_the_error_path_produces_no_absolute_workspace_path(
    tmp_path: Path, frozen_now: str
) -> None:
    """The exact case VS2 shipped wrong."""
    layout = initialize(tmp_path / "ws", project_name="p")
    break_a_record(layout)

    report = run_doctor(Layout(root=layout.root))
    assert report.has_errors, "the fixture must actually produce an error"

    rendered = json.dumps(report.to_document())
    assert str(layout.root) not in rendered
    assert WORKSPACE_TOKEN in rendered


def test_equivalent_workspaces_under_different_roots_compare_equal(
    tmp_path: Path, frozen_now: str
) -> None:
    """The comparability half of the contract, on the error path."""
    reports: list[dict[str, Any]] = []
    for name in ("alpha", "a-much-longer-directory-name"):
        layout = initialize(tmp_path / name / "ws", project_name="p")
        break_a_record(layout)
        reports.append(run_doctor(Layout(root=layout.root)).to_document())

    assert reports[0] == reports[1]
    assert json.dumps(reports[0], sort_keys=True) == json.dumps(reports[1], sort_keys=True)


def test_equivalent_workspaces_compare_equal_for_a_location_mismatch(
    tmp_path: Path, frozen_now: str
) -> None:
    """Also on the finding type this slice introduces."""
    reports: list[dict[str, Any]] = []
    for name in ("b", "another-much-longer-name"):
        layout = initialize(tmp_path / name / "ws", project_name="p")
        store = SkillStore(layout)
        skill = store.create(
            name="Target",
            scope="project",
            purpose="p",
            applies_when=["a"],
            do_not_apply_when=["b"],
            activation_rules=ACTIVATION,
            created_from=["manual:t"],
        )
        path = store.registry.path_of(skill.id)
        document = load_yaml_file(path)
        document["classification"]["scope"] = "core"
        write_yaml_file(path, document)
        reports.append(run_doctor(Layout(root=layout.root)).to_document())

    assert reports[0] == reports[1]


def test_output_remains_byte_stable_across_runs(tmp_path: Path, frozen_now: str) -> None:
    """Normalization must not have cost the property VS2 did get right."""
    layout = initialize(tmp_path / "ws", project_name="p")
    break_a_record(layout)
    first = json.dumps(run_doctor(Layout(root=layout.root)).to_document(), sort_keys=True)
    second = json.dumps(run_doctor(Layout(root=layout.root)).to_document(), sort_keys=True)
    assert first == second


def test_lower_level_exceptions_keep_their_absolute_paths(tmp_path: Path, frozen_now: str) -> None:
    """Sanitization belongs at the report boundary, not in the exception.

    An absolute path is exactly what a developer wants from a traceback; only
    the machine-readable report promises workspace independence.
    """
    from skillkernel.core.errors import IntegrityError

    layout = initialize(tmp_path / "ws", project_name="p")
    break_a_record(layout)
    store = SkillStore(Layout(root=layout.root))

    with pytest.raises(IntegrityError) as excinfo:
        store.get("SKILL-0001")
    assert str(layout.root) in str(excinfo.value)
