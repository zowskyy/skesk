"""Vertical Slice 4: a bundled skill, delivered by the wheel.

VS2 proved the installed console script runs. It did not prove the package
*ships* anything besides code, and that is the failure mode this slice is
exposed to: ``assets/`` is data, and data is included in a wheel only if the
build backend has been told to include it. A bundle that loads perfectly in the
checkout and is absent from ``site-packages`` is a shipping failure that every
in-repository test would miss.

So this module builds a real wheel, installs it into a fresh interpreter that
has never seen this repository, and drives the lifecycle from there. The
checkout is used for exactly one thing: as the source the wheel is built from.

No step here may be skipped. A missing build backend, a missing wheel or a
missing asset is an assertion failure -- a skip would hide the defect the
module exists to catch.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.acceptance

# Exit codes, per DEC-0010.
EXIT_OK = 0
EXIT_INTEGRITY = 1
EXIT_USAGE = 2
EXIT_NOT_INITIALIZED = 3

BUNDLE = "two-method-escalation"
REPO_ROOT = Path(__file__).resolve().parents[2]

EXPECTED_ASSETS = {
    "skillkernel/assets/skills/two-method-escalation/bundle.yaml",
    "skillkernel/assets/skills/two-method-escalation/examples/negative/"
    "blocked-but-first-attempt-underway.yaml",
    "skillkernel/assets/skills/two-method-escalation/examples/negative/"
    "first-attempt-still-running.yaml",
    "skillkernel/assets/skills/two-method-escalation/examples/negative/hard-but-progressing.yaml",
    "skillkernel/assets/skills/two-method-escalation/examples/negative/routine-work.yaml",
    "skillkernel/assets/skills/two-method-escalation/examples/positive/ambiguous-requirement.yaml",
    "skillkernel/assets/skills/two-method-escalation/examples/positive/"
    "blocked-after-two-methods.yaml",
    "skillkernel/assets/skills/two-method-escalation/examples/positive/"
    "blocked-on-authorization.yaml",
}


def run(*command: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run a command from a directory outside this repository.

    ``cwd`` is required, and that is not fussiness. Python puts the working
    directory on ``sys.path``, so a subprocess started from the checkout
    imports *this* ``skillkernel/`` package no matter what it installed. The
    first draft of this module did exactly that, and the guard in the
    ``installed`` fixture is what caught it -- every assertion below would
    otherwise have been testing the checkout while claiming to test the wheel.
    """
    return subprocess.run(list(command), cwd=str(cwd), capture_output=True, text=True)


@pytest.fixture(scope="module")
def outside(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A working directory with no relationship to this repository."""
    return tmp_path_factory.mktemp("outside")


@pytest.fixture(scope="module")
def wheel(tmp_path_factory: pytest.TempPathFactory, outside: Path) -> Path:
    """Build a wheel from this checkout.

    ``--no-build-isolation`` keeps the build offline: the backend is already
    installed in the development environment, so no index is consulted.
    """
    target = tmp_path_factory.mktemp("wheel")
    result = run(
        sys.executable,
        "-m",
        "pip",
        "wheel",
        "--no-deps",
        "--no-build-isolation",
        "-w",
        str(target),
        str(REPO_ROOT),
        cwd=outside,
    )
    assert result.returncode == 0, f"building the wheel failed:\n{result.stdout}\n{result.stderr}"
    wheels = sorted(target.glob("skillkernel-*.whl"))
    assert len(wheels) == 1, f"expected exactly one wheel, found {wheels}"
    return wheels[0]


@pytest.fixture(scope="module")
def installed(wheel: Path, tmp_path_factory: pytest.TempPathFactory, outside: Path) -> Path:
    """A fresh interpreter whose only SkillKernel is the built wheel.

    PyYAML is copied in rather than downloaded, so the whole fixture is offline
    and deterministic. Nothing from this repository is on the new interpreter's
    path: an editable install would defeat the entire point of the module.
    """
    venv = tmp_path_factory.mktemp("venv") / "clean"
    created = run(sys.executable, "-m", "venv", str(venv), cwd=outside)
    assert created.returncode == 0, f"creating the venv failed:\n{created.stderr}"

    python = venv / "bin" / "python"
    assert python.is_file(), f"no interpreter at {python}"

    result = run(
        str(python), "-m", "pip", "install", "--no-deps", "--no-index", str(wheel), cwd=outside
    )
    assert result.returncode == 0, f"installing the wheel failed:\n{result.stdout}\n{result.stderr}"

    site = next((venv / "lib").glob("python3.*/site-packages"))
    source = Path(yaml.__file__).parent
    shutil.copytree(source, site / "yaml")
    for extension in source.parent.glob("_yaml*"):
        if extension.is_dir():
            shutil.copytree(extension, site / extension.name)
        else:
            shutil.copy2(extension, site / extension.name)

    # The repository must not be reachable from here, or nothing below is proof.
    located = run(str(python), "-c", "import skillkernel; print(skillkernel.__file__)", cwd=outside)
    assert located.returncode == 0, located.stderr
    resolved = Path(located.stdout.strip()).resolve()
    assert str(resolved).startswith(str(site.resolve())), (
        f"the fresh interpreter resolved skillkernel to {resolved}, not to the "
        f"wheel it installed at {site}"
    )
    return venv


@pytest.fixture(scope="module")
def executable(installed: Path) -> Path:
    candidate = installed / "bin" / "skillkernel"
    if os.name == "nt":  # pragma: no cover - the CI platform is POSIX
        candidate = candidate.with_suffix(".exe")
    assert candidate.is_file(), (
        f"the wheel installed no console script at {candidate}; "
        "pyproject.toml declares skillkernel = 'skillkernel.cli.main:main'"
    )
    return candidate


@pytest.fixture
def workspace(executable: Path, tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    result = run(str(executable), "init", str(root), cwd=tmp_path)
    assert result.returncode == EXIT_OK, result.stderr
    return root


# --- what the wheel contains -----------------------------------------------


def test_the_wheel_ships_every_bundle_file(wheel: Path) -> None:
    """The check that a checkout can never make: is the data actually in there?"""
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
    missing = EXPECTED_ASSETS - names
    assert not missing, f"the built wheel is missing {sorted(missing)}"


def test_the_installed_package_reads_the_bundle_from_site_packages(
    installed: Path, outside: Path
) -> None:
    result = run(
        str(installed / "bin" / "python"),
        "-c",
        "from skillkernel.bundles.catalog import load_bundle, packaged_root; "
        "b = load_bundle('two-method-escalation'); "
        "print(packaged_root()); print(b.content_hash)",
        cwd=outside,
    )
    assert result.returncode == 0, result.stderr
    root, digest = result.stdout.strip().splitlines()
    assert "site-packages" in root, f"the bundle was read from {root}, not from the installation"
    assert digest.startswith("sha256:")


def test_the_portable_hash_is_the_same_in_the_wheel_and_the_checkout(
    installed: Path, outside: Path
) -> None:
    """The container must not change the content hash. Only the content may."""
    from skillkernel.bundles.catalog import load_bundle

    result = run(
        str(installed / "bin" / "python"),
        "-c",
        "from skillkernel.bundles.catalog import load_bundle; "
        "print(load_bundle('two-method-escalation').content_hash)",
        cwd=outside,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == load_bundle(BUNDLE).content_hash


# --- the installed command -------------------------------------------------


def test_install_reports_what_it_did(executable: Path, workspace: Path, outside: Path) -> None:
    result = run(str(executable), "skill", "install", BUNDLE, str(workspace), cwd=outside)
    assert result.returncode == EXIT_OK, result.stderr
    assert "SKILL-0001" in result.stdout
    assert f"core/{BUNDLE}/skill.yaml" in result.stdout


def test_the_skill_lands_at_a_canonical_location(
    executable: Path, workspace: Path, outside: Path
) -> None:
    run(str(executable), "skill", "install", BUNDLE, str(workspace), cwd=outside)
    record = workspace / "skills" / "core" / BUNDLE / "skill.yaml"
    assert record.is_file()
    assert (workspace / "skills" / "core" / BUNDLE / "history.yaml").is_file()
    assert (workspace / "skills" / "core" / BUNDLE / "scorer" / "eval.yaml").is_file()


def test_nothing_is_written_outside_the_workspace(
    executable: Path, workspace: Path, outside: Path
) -> None:
    """A slug is a path component, so nothing can be written above the workspace.

    Looking only inside the workspace would be vacuous -- a path that escaped it
    is, by definition, not there to be found. So this looks at the parent, which
    is where an escaped write would land.
    """
    parent = workspace.parent
    before = set(parent.iterdir())
    assert before == {workspace}

    run(str(executable), "skill", "install", BUNDLE, str(workspace), cwd=outside)
    assert set(parent.iterdir()) == before

    # And every installed file is under the canonical scope directory.
    skills = workspace / "skills"
    installed = {path.relative_to(skills).parts[0] for path in skills.rglob("*") if path.is_file()}
    assert installed == {"core", "registry"}


def test_the_installed_record_imports_no_lifecycle_state(
    executable: Path, workspace: Path, outside: Path
) -> None:
    run(str(executable), "skill", "install", BUNDLE, str(workspace), cwd=outside)
    document = yaml.safe_load(
        (workspace / "skills" / "core" / BUNDLE / "skill.yaml").read_text(encoding="utf-8")
    )
    assert document["id"] == "SKILL-0001"
    assert document["classification"]["maturity"] == "observed"
    assert document["confidence"] == "low"
    assert document["evidence"] == {"experiments": [], "knowledge": [], "records": []}
    assert document["version"] == "0.1.0"
    assert set(document["provenance"]["x_source"]) == {
        "bundle_id",
        "bundle_version",
        "content_hash",
    }

    history = yaml.safe_load(
        (workspace / "skills" / "core" / BUNDLE / "history.yaml").read_text(encoding="utf-8")
    )
    assert [entry["new_state"] for entry in history["transitions"]] == ["observed"]
    assert history["transitions"][0]["previous_state"] is None
    assert history["transitions"][0]["evidence"] == [], "the bundle imported evidence"


def test_doctor_passes_on_the_installed_workspace(
    executable: Path, workspace: Path, outside: Path
) -> None:
    run(str(executable), "skill", "install", BUNDLE, str(workspace), cwd=outside)
    result = run(str(executable), "doctor", str(workspace), cwd=outside)
    assert result.returncode == EXIT_OK, f"{result.stdout}\n{result.stderr}"

    machine = run(str(executable), "doctor", "--json", str(workspace), cwd=outside)
    report = json.loads(machine.stdout)
    assert report["counts"]["error"] == 0
    assert str(workspace) not in machine.stdout, "the JSON report leaked an absolute path"


def test_installing_twice_is_refused_and_changes_nothing(
    executable: Path, workspace: Path, outside: Path
) -> None:
    run(str(executable), "skill", "install", BUNDLE, str(workspace), cwd=outside)
    before = {
        path.relative_to(workspace).as_posix(): path.read_bytes()
        for path in sorted(workspace.rglob("*"))
        if path.is_file()
    }
    assert before, "nothing was installed; the comparison below would be vacuous"

    result = run(str(executable), "skill", "install", BUNDLE, str(workspace), cwd=outside)
    assert result.returncode == EXIT_INTEGRITY
    assert "already exists" in result.stderr

    after = {
        path.relative_to(workspace).as_posix(): path.read_bytes()
        for path in sorted(workspace.rglob("*"))
        if path.is_file()
    }
    assert after == before


def test_an_unknown_bundle_is_refused(executable: Path, workspace: Path, outside: Path) -> None:
    result = run(str(executable), "skill", "install", "no-such-bundle", str(workspace), cwd=outside)
    assert result.returncode == EXIT_INTEGRITY
    assert BUNDLE in result.stderr, "the refusal should name what is available"


def test_installing_into_an_uninitialized_directory_is_refused(
    executable: Path, tmp_path: Path, outside: Path
) -> None:
    result = run(
        str(executable), "skill", "install", BUNDLE, str(tmp_path / "nowhere"), cwd=outside
    )
    assert result.returncode == EXIT_NOT_INITIALIZED


def test_skill_without_a_subcommand_is_a_usage_error(executable: Path, outside: Path) -> None:
    assert run(str(executable), "skill", cwd=outside).returncode == EXIT_USAGE


# --- the lifecycle the install makes possible ------------------------------

LIFECYCLE = """
import json, sys
from pathlib import Path
from skillkernel.core.paths import Layout
from skillkernel.evidence.ledger import EvidenceLedger
from skillkernel.evaluation.runner import evaluate_skill
from skillkernel.promotion.engine import PromotionEngine
from skillkernel.skills.store import SkillStore

layout = Layout(root=Path(sys.argv[1]).resolve())
store = SkillStore(layout)
skill_id = store.ids()[0]

engine = PromotionEngine(layout)
promoted = engine.promote(
    skill_id, "candidate", reason="The installed definition is complete.", actor="acceptance"
)
report = evaluate_skill(layout, skill_id, project="acceptance")

reloaded = SkillStore(Layout(root=Path(sys.argv[1]).resolve())).get(skill_id)
evidence = EvidenceLedger(layout).get(report.evidence_id)
try:
    engine.promote(skill_id, "experimental", reason="Try.", actor="acceptance")
    experimental = "PROMOTED"
except Exception as exc:
    experimental = type(exc).__name__

print(json.dumps({
    "skill_id": skill_id,
    "maturity": promoted.maturity,
    "verdict": report.verdict,
    "evidence_id": report.evidence_id,
    "fingerprint": report.skill_fingerprint,
    "reloaded_maturity": reloaded.maturity,
    "reloaded_source": reloaded.provenance.get("x_source"),
    "evidence_skill": evidence.links["skill"],
    "evidence_fingerprint": evidence.links["skill_fingerprint"],
    "experimental": experimental,
}))
"""


def test_the_installed_skill_earns_its_own_evidence(
    executable: Path, installed: Path, workspace: Path, outside: Path
) -> None:
    """The honest endpoint: candidate, on evidence this workspace produced."""
    run(str(executable), "skill", "install", BUNDLE, str(workspace), cwd=outside)
    result = run(str(installed / "bin" / "python"), "-c", LIFECYCLE, str(workspace), cwd=outside)
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    outcome = json.loads(result.stdout)

    assert outcome["maturity"] == "candidate"
    assert outcome["verdict"] == "pass"
    assert outcome["evidence_id"] == "EV-0001", "the evidence was not allocated locally"
    assert outcome["fingerprint"], "no local fingerprint was computed"

    # The evidence is about this workspace's skill, under this workspace's
    # fingerprint. A bundle cannot supply either.
    assert outcome["evidence_skill"] == outcome["skill_id"]
    assert outcome["evidence_fingerprint"] == outcome["fingerprint"]

    # Source provenance survives the lifecycle, and stays distinct from it.
    assert outcome["reloaded_maturity"] == "candidate"
    assert set(outcome["reloaded_source"]) == {"bundle_id", "bundle_version", "content_hash"}
    assert outcome["reloaded_source"]["bundle_id"] == BUNDLE

    # And the state a bundle must never be able to reach.
    assert outcome["experimental"] == "GateError"


def test_doctor_is_still_clean_after_the_lifecycle(
    executable: Path, installed: Path, workspace: Path, outside: Path
) -> None:
    run(str(executable), "skill", "install", BUNDLE, str(workspace), cwd=outside)
    lifecycle = run(str(installed / "bin" / "python"), "-c", LIFECYCLE, str(workspace), cwd=outside)
    assert lifecycle.returncode == 0, lifecycle.stderr
    result = run(str(executable), "doctor", str(workspace), cwd=outside)
    assert result.returncode == EXIT_OK, f"{result.stdout}\n{result.stderr}"
