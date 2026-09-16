"""Vertical Slice 7: an evidence artifact belongs to its record, proved from the wheel.

The artifact namespace is the one place a workspace keeps bytes that evidence
vouches for. Until VS7 a write could adopt a directory nobody owned, a corrupt
record could make verification read a file outside the repository, and anything
at all could sit unreferenced beside a real artifact. These prove the closed
world holds for what actually ships.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.acceptance

REPO_ROOT = Path(__file__).resolve().parents[2]


def run(*command: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run a command from a directory outside this repository.

    ``cwd`` is required for the reason VS4 documented: Python puts the working
    directory on ``sys.path``, so a subprocess started from the checkout imports
    *this* package however the wheel was installed.
    """
    return subprocess.run(list(command), cwd=str(cwd), capture_output=True, text=True)


@pytest.fixture(scope="module")
def outside(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("outside")


@pytest.fixture(scope="module")
def wheel(tmp_path_factory: pytest.TempPathFactory, outside: Path) -> Path:
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

    located = run(str(python), "-c", "import skillkernel; print(skillkernel.__file__)", cwd=outside)
    assert located.returncode == 0, located.stderr
    resolved = Path(located.stdout.strip()).resolve()
    assert str(resolved).startswith(str(site.resolve())), (
        f"the fresh interpreter resolved skillkernel to {resolved}, not to the wheel at {site}"
    )
    return venv


DRIVER = textwrap.dedent(
    """
    import json, os, sys, hashlib
    from pathlib import Path
    os.environ["SKILLKERNEL_NOW"] = "2024-01-31T12:00:00Z"
    import skillkernel
    assert "site-packages" in skillkernel.__file__, skillkernel.__file__

    from skillkernel.project.bootstrap import initialize
    from skillkernel.evidence.ledger import EvidenceLedger
    from skillkernel.skills.store import SkillStore
    from skillkernel.evaluation.suite import write_evaluation_suite
    from skillkernel.evaluation.runner import evaluate_skill
    from skillkernel.promotion.gates import verifiable_evaluations
    from skillkernel.validation.doctor import run_doctor
    from skillkernel.core.errors import SkillKernelError
    import yaml

    ACT = {"require_any": ["alpha"], "require_all": [], "exclude_any": ["beta"]}
    base = Path(sys.argv[1])
    out = {}

    def ws(tag):
        return initialize(base / tag / "ws", project_name="acceptance", now="2024-01-31T12:00:00Z")
    def rec(L, body=b"REAL", name="report.txt"):
        return EvidenceLedger(L).record(kind="observation_note", summary="s",
            project="acceptance", source_type="tool", source_detail="acceptance",
            artifact_bytes=body, artifact_name=name)
    def errs(L):
        r = run_doctor(L)
        return [f for f in r.findings if f.severity == "ERROR"]

    L = ws("normal"); r = rec(L)
    out["1_normal_record_succeeds"] = r.id == "EV-0001" and EvidenceLedger(L).verify() == []

    L = ws("adopt"); led = EvidenceLedger(L); saved = led.registry.index_file.read_bytes()
    first = rec(L, b"SENTINEL")
    art = L.evidence_artifacts_dir / first.id / "report.txt"; before = art.read_bytes()
    led.registry.path_of(first.id).unlink(); led.registry.index_file.write_bytes(saved)
    seq = EvidenceLedger(L).registry.load_index().next_sequence
    try:
        rec(L, b"REPLACEMENT"); refused = False
    except SkillKernelError:
        refused = True
    out["2_preexisting_destination_refused"] = refused
    out["2_sentinel_intact"] = art.read_bytes() == before
    out["2_no_id_burn"] = EvidenceLedger(L).registry.load_index().next_sequence == seq

    L = ws("extra"); r = rec(L)
    (L.evidence_artifacts_dir / r.id / "smuggled.txt").write_text("x")
    out["3_extra_file_is_error"] = len(errs(L)) > 0

    L = ws("nested"); r = rec(L)
    n = L.evidence_artifacts_dir / r.id / "nested"; n.mkdir(); (n / "d.bin").write_bytes(b"x")
    out["4_nested_is_error"] = len(errs(L)) > 0

    L = ws("null"); r = EvidenceLedger(L).record(kind="observation_note", summary="s",
        project="acceptance", source_type="tool", source_detail="acceptance")
    (L.evidence_artifacts_dir / r.id).mkdir(parents=True)
    out["5_null_artifact_dir_is_error"] = len(errs(L)) > 0

    L = ws("outside"); r = rec(L)
    outside = base / "outside"; outside.mkdir(parents=True, exist_ok=True)
    secret = outside / "secret.txt"; secret.write_text("SECRET")
    p = EvidenceLedger(L).registry.path_of(r.id)
    doc = yaml.safe_load(p.read_text()); doc["artifact"]["path"] = "../../outside/secret.txt"
    p.write_text(yaml.safe_dump(doc))
    target = str(L.root / "../../outside/secret.txt")
    touched = {"n": 0}; real = Path.stat
    def spy(self, *a, **k):
        if str(self) == target: touched["n"] += 1
        return real(self, *a, **k)
    Path.stat = spy
    issues = EvidenceLedger(L).verify()
    Path.stat = real
    out["6_outside_path_refused"] = any("artifact" in str(i) for i in issues)
    out["6_target_never_touched"] = touched["n"] == 0

    L = ws("vs6"); s = SkillStore(L)
    sk = s.create(name="S", scope="core", slug="s", purpose="p", applies_when=["alpha"],
                  do_not_apply_when=["beta"], activation_rules=ACT)
    s.update(sk.id, procedure=["do"], success_conditions=["ok"], failure_modes=["no"],
             verification=["look"])
    write_evaluation_suite(L, sk.id, corpus_id="a", pass_threshold=0.0,
        max_false_activation_rate=1.0, positive=[{"case_id": "p", "signals": ["alpha"]}],
        negative=[{"case_id": "n", "signals": ["beta"]}])
    e = evaluate_skill(L, sk.id, project="acceptance")
    out["7_vs6_snapshot_still_verifies"] = [x.id for x in
        verifiable_evaluations(L, s.get(sk.id))] == [e.evidence_id]
    out["8_clean_workspace_clean_doctor"] = run_doctor(L).findings == []

    print(json.dumps(out))
    """
)


@pytest.fixture(scope="module")
def results(installed: Path, outside: Path, tmp_path_factory: pytest.TempPathFactory) -> dict:
    workspace = tmp_path_factory.mktemp("slice7")
    script = workspace / "driver.py"
    script.write_text(DRIVER, encoding="utf-8")
    result = run(str(installed / "bin" / "python"), str(script), str(workspace), cwd=outside)
    assert result.returncode == 0, f"the installed driver failed:\n{result.stdout}\n{result.stderr}"
    return dict(json.loads(result.stdout.strip().splitlines()[-1]))


@pytest.mark.parametrize(
    "claim",
    [
        "1_normal_record_succeeds",
        "2_preexisting_destination_refused",
        "2_sentinel_intact",
        "2_no_id_burn",
        "3_extra_file_is_error",
        "4_nested_is_error",
        "5_null_artifact_dir_is_error",
        "6_outside_path_refused",
        "6_target_never_touched",
        "7_vs6_snapshot_still_verifies",
        "8_clean_workspace_clean_doctor",
    ],
)
def test_the_installed_package_upholds(results: dict, claim: str) -> None:
    assert results[claim] is True, f"{claim} did not hold in the installed package"
