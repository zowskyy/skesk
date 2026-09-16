"""Vertical Slice 2: the installed CLI boundary.

This test exists because of a specific, embarrassing discovery. At the end of
VS1 the repository had 483 passing tests, clean lint, clean types and a
clean-checkout reproduction — while the command it declares in
``[project.scripts]`` did not run at all:

    $ skillkernel --help
    ModuleNotFoundError: No module named 'skillkernel.cli'

Every test imported the package directly. Nothing ever executed what was
installed. The declared product interface was broken and the whole suite was
green.

So the rule here is strict: **this module invokes the generated console
executable through subprocess.** Importing ``main()``, or calling CLI functions
in-process, would recreate exactly the blind spot that let the defect survive.
``python -m skillkernel`` is checked too, but only as a *second* surface — it
resolves through a different mechanism and cannot substitute for the
entry-point script that actually broke.

A missing executable is an assertion failure, never a skip. A skip would hide
the defect this test was written to catch.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.acceptance

# Exit codes, per DEC-0010.
EXIT_OK = 0
EXIT_INTEGRITY = 1
EXIT_USAGE = 2
EXIT_NOT_INITIALIZED = 3
EXIT_INTERNAL = 70


def console_script() -> Path:
    """Locate the executable generated from [project.scripts]."""
    candidate = Path(sys.executable).parent / "skillkernel"
    if os.name == "nt":  # pragma: no cover - the CI platform is POSIX
        candidate = candidate.with_suffix(".exe")
    assert candidate.is_file(), (
        f"the installed console script is missing at {candidate}. "
        "pyproject.toml declares skillkernel = 'skillkernel.cli.main:main'; "
        "install the package with 'pip install -e .[dev]'. This assertion is "
        "deliberately not a skip: a missing entry point is the exact defect "
        "this module exists to catch."
    )
    return candidate


def run_cli(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run the installed executable from a directory outside the repository."""
    return subprocess.run(
        [str(console_script()), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


def run_module(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    """The second surface: python -m skillkernel."""
    return subprocess.run(
        [sys.executable, "-m", "skillkernel", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


def fingerprint_tree(root: Path) -> dict[str, str]:
    """Hash every file under ``root``, for byte-identical comparisons.

    Refuses an empty or missing tree. ``Path.rglob`` returns an empty iterator
    for a directory that does not exist, so without this guard a before/after
    comparison would compare ``{} == {}`` and pass while nothing had been
    created at all. That vacuous pass occurred while writing this module.
    """
    assert root.is_dir(), f"{root} is not a directory; nothing to fingerprint"
    digests: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            digests[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    assert digests, f"{root} contains no files; a comparison would be vacuous"
    return digests


@pytest.fixture
def outside(tmp_path: Path) -> Path:
    """A working directory outside the repository.

    Running from inside the repo would let the CLI resolve this project's own
    workspace by walking upwards, which would mask a path-handling bug.
    """
    directory = tmp_path / "elsewhere"
    directory.mkdir()
    return directory


# --- the entry point itself ------------------------------------------------


def test_the_installed_console_script_exists_and_runs(outside: Path) -> None:
    """The single assertion that would have caught the VS1 defect."""
    result = run_cli("--help", cwd=outside)
    assert result.returncode == EXIT_OK, f"stderr: {result.stderr}"
    assert "init" in result.stdout
    assert "doctor" in result.stdout


def test_the_module_surface_also_works(outside: Path) -> None:
    result = run_module("--help", cwd=outside)
    assert result.returncode == EXIT_OK, f"stderr: {result.stderr}"


def test_help_documents_the_exit_codes(outside: Path) -> None:
    """DEC-0010 is a user-facing contract, so it belongs in --help."""
    result = run_cli("--help", cwd=outside)
    assert "70" in result.stdout


# --- init ------------------------------------------------------------------


def test_init_creates_a_workspace(outside: Path, tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    result = run_cli("init", str(workspace), cwd=outside)

    assert result.returncode == EXIT_OK, f"stderr: {result.stderr}"
    assert (workspace / "skillkernel.yaml").is_file()
    assert (workspace / "docs" / "project" / "profile.yaml").is_file()
    assert "skillkernel.yaml" in result.stdout or "Initialized" in result.stdout


def test_init_accepts_a_project_name(outside: Path, tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    result = run_cli("init", str(workspace), "--project-name", "demo-project", cwd=outside)
    assert result.returncode == EXIT_OK
    assert "demo-project" in (workspace / "docs" / "project" / "profile.yaml").read_text()


def test_re_running_init_refuses_and_changes_nothing(outside: Path, tmp_path: Path) -> None:
    """Idempotency as the directive actually demands it: no corruption."""
    workspace = tmp_path / "ws"
    assert run_cli("init", str(workspace), cwd=outside).returncode == EXIT_OK

    before = fingerprint_tree(workspace)
    result = run_cli("init", str(workspace), cwd=outside)
    after = fingerprint_tree(workspace)

    assert result.returncode != EXIT_OK
    assert result.returncode != EXIT_INTERNAL, "a refusal is expected, not a crash"
    assert "already" in result.stderr.lower() or "refus" in result.stderr.lower()
    assert after == before, "a refused re-init must not modify the workspace"


# --- doctor ----------------------------------------------------------------


def test_doctor_reports_a_healthy_workspace(outside: Path, tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    run_cli("init", str(workspace), cwd=outside)

    result = run_cli("doctor", str(workspace), cwd=outside)
    assert result.returncode == EXIT_OK, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert "error" not in result.stdout.lower() or "0 error" in result.stdout.lower()


def test_doctor_is_read_only(outside: Path, tmp_path: Path) -> None:
    """Diagnosis must never mutate the thing being diagnosed."""
    workspace = tmp_path / "ws"
    run_cli("init", str(workspace), cwd=outside)

    before = fingerprint_tree(workspace)
    run_cli("doctor", str(workspace), cwd=outside)
    assert fingerprint_tree(workspace) == before


def test_doctor_json_is_valid_and_byte_stable(outside: Path, tmp_path: Path) -> None:
    """Deterministic output is what makes the report usable by other tools."""
    workspace = tmp_path / "ws"
    run_cli("init", str(workspace), cwd=outside)

    first = run_cli("doctor", str(workspace), "--json", cwd=outside)
    second = run_cli("doctor", str(workspace), "--json", cwd=outside)

    assert first.returncode == EXIT_OK
    document = json.loads(first.stdout)
    assert "findings" in document
    assert first.stdout == second.stdout, "--json must be byte-identical across runs"


def test_doctor_detects_a_tampered_evidence_artifact(outside: Path, tmp_path: Path) -> None:
    """The end-to-end point: a real integrity breach reaches the exit code."""
    workspace = tmp_path / "ws"
    run_cli("init", str(workspace), cwd=outside)

    # Produce a real evidence record through the domain layer, then corrupt it.
    script = (
        "from pathlib import Path\n"
        "from skillkernel.core.paths import Layout\n"
        "from skillkernel.evidence.ledger import EvidenceLedger\n"
        f"layout = Layout(root=Path({str(workspace)!r}))\n"
        "record = EvidenceLedger(layout).record(\n"
        "    kind='command_output', summary='a run', project='p',\n"
        "    source_type='command', source_detail='pytest',\n"
        "    artifact_bytes=b'original', artifact_name='out.txt')\n"
        "print(record.artifact['path'])\n"
    )
    created = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    artifact = workspace / created.stdout.strip()
    assert artifact.is_file()
    artifact.write_text("tampered")

    result = run_cli("doctor", str(workspace), cwd=outside)
    assert result.returncode == EXIT_INTEGRITY
    assert "out.txt" in (result.stdout + result.stderr)


def test_doctor_on_an_uninitialized_directory(outside: Path, tmp_path: Path) -> None:
    empty = tmp_path / "not-a-workspace"
    empty.mkdir()
    result = run_cli("doctor", str(empty), cwd=outside)
    assert result.returncode == EXIT_NOT_INITIALIZED
    assert "init" in (result.stdout + result.stderr).lower()


# --- usage errors ----------------------------------------------------------


def test_an_unknown_command_is_a_usage_error(outside: Path) -> None:
    assert run_cli("nonsense", cwd=outside).returncode == EXIT_USAGE


def test_a_missing_argument_is_a_usage_error(outside: Path) -> None:
    assert run_cli("init", cwd=outside).returncode == EXIT_USAGE


def test_no_command_is_a_usage_error(outside: Path) -> None:
    assert run_cli(cwd=outside).returncode == EXIT_USAGE


# --- the two surfaces agree ------------------------------------------------


def test_both_surfaces_agree_on_a_healthy_workspace(outside: Path, tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    run_cli("init", str(workspace), cwd=outside)

    via_script = run_cli("doctor", str(workspace), "--json", cwd=outside)
    via_module = run_module("doctor", str(workspace), "--json", cwd=outside)

    assert via_script.returncode == via_module.returncode == EXIT_OK
    assert via_script.stdout == via_module.stdout
