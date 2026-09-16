"""Vertical Slice 6: evaluation evidence that can still be checked, from the wheel.

The defect this slice closes was invisible to every in-repository assumption:
an evaluation's verdict outlived the inputs that produced it. Deleting the
guardrail corpus -- or the whole suite -- left the passing evidence counting
toward promotion with ``doctor`` silent.

The fix has three parts, and each is proved here through the installed package
rather than the checkout: live input identity for ``validated``, an immutable
snapshot written with every evaluation, and corpus-content identity for
``trusted`` so that a relabelled corpus is not a second corpus.

The whole point is that these hold for what actually ships, so nothing here may
be skipped: a missing build backend or a missing wheel is an assertion failure.
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
    import json, os, sys
    from pathlib import Path
    os.environ["SKILLKERNEL_NOW"] = "2024-01-31T12:00:00Z"
    import skillkernel
    assert "site-packages" in skillkernel.__file__, skillkernel.__file__

    from skillkernel.project.bootstrap import initialize
    from skillkernel.skills.store import SkillStore
    from skillkernel.evaluation.suite import write_evaluation_suite, load_evaluation_suite
    from skillkernel.evaluation.runner import evaluate_skill
    from skillkernel.promotion.gates import passing_evaluations, verifiable_evaluations
    from skillkernel.evidence.ledger import EvidenceLedger
    from skillkernel.validation.doctor import run_doctor
    import hashlib

    ACT = {"require_any": ["alpha"], "require_all": [], "exclude_any": ["beta"]}
    root = Path(sys.argv[1]) / "ws"
    layout = initialize(root, project_name="acceptance", now="2024-01-31T12:00:00Z")
    store = SkillStore(layout)
    skill = store.create(name="Subject", scope="core", slug="subject", purpose="p",
        applies_when=["alpha"], do_not_apply_when=["beta"], activation_rules=ACT)
    store.update(skill.id, procedure=["do"], success_conditions=["ok"],
                 failure_modes=["no"], verification=["look"])

    A = dict(corpus_id="corpus-a",
             positive=[{"case_id": "shared", "signals": ["alpha"]}],
             negative=[{"case_id": "a-neg", "signals": ["beta"]}])
    B = dict(corpus_id="corpus-b",
             positive=[{"case_id": "shared", "signals": ["alpha", "extra"]}],
             negative=[{"case_id": "b-neg", "signals": ["beta"]}])

    def author(c):
        return write_evaluation_suite(layout, skill.id, corpus_id=c["corpus_id"],
            pass_threshold=0.0, max_false_activation_rate=1.0,
            positive=c["positive"], negative=c["negative"])

    def artifact(evidence_id):
        rec = EvidenceLedger(layout).get(evidence_id)
        return layout.root / str(rec.artifact["path"])

    out = {}

    author(A)
    first = evaluate_skill(layout, skill.id, project="acceptance")
    snap_a = artifact(first.evidence_id)
    out["1_snapshot_a_written"] = json.loads(snap_a.read_text())["inputs"]["documents"] != {}
    digest_a_before = hashlib.sha256(snap_a.read_bytes()).hexdigest()

    author(B)
    live = load_evaluation_suite(layout, skill.id)
    out["2_live_is_b_not_union"] = sorted(c.case_id for c in live.cases) == ["b-neg", "shared"]

    second = evaluate_skill(layout, skill.id, project="acceptance")
    out["3_snapshot_b_written"] = artifact(second.evidence_id).is_file()

    sk = store.get(skill.id)
    out["4_current_is_only_b"] = [r.id for r in passing_evaluations(layout, sk)] == [
        second.evidence_id]
    out["5_trusted_counts_both"] = sorted(
        r.id for r in verifiable_evaluations(layout, sk)) == sorted(
        [first.evidence_id, second.evidence_id])
    out["5_digests_differ"] = first.corpus_content_digest != second.corpus_content_digest

    out["8_snapshot_a_byte_identical"] = (
        hashlib.sha256(snap_a.read_bytes()).hexdigest() == digest_a_before)

    rep = run_doctor(layout)
    out["9_doctor_clean"] = rep.is_complete and not rep.has_errors and not rep.warnings

    # 6: relabelling identical content must not manufacture a second corpus.
    author({**B, "corpus_id": "corpus-b-renamed"})
    third = evaluate_skill(layout, skill.id, project="acceptance")
    out["6_relabel_is_same_corpus"] = (
        third.corpus_content_digest == second.corpus_content_digest)
    sk = store.get(skill.id)
    out["6_distinct_corpora_still_two"] = len({
        r.attributes["corpus_content_digest"] for r in verifiable_evaluations(layout, sk)}) == 2

    # 7: a corrupt snapshot removes that evaluation from trusted.
    artifact(first.evidence_id).unlink()
    sk = store.get(skill.id)
    out["7_corrupt_snapshot_drops_out"] = first.evidence_id not in [
        r.id for r in verifiable_evaluations(layout, sk)]

    print(json.dumps(out))
    """
)


@pytest.fixture(scope="module")
def results(installed: Path, outside: Path, tmp_path_factory: pytest.TempPathFactory) -> dict:
    workspace = tmp_path_factory.mktemp("slice6")
    script = workspace / "driver.py"
    script.write_text(DRIVER, encoding="utf-8")
    result = run(str(installed / "bin" / "python"), str(script), str(workspace), cwd=outside)
    assert result.returncode == 0, f"the installed driver failed:\n{result.stdout}\n{result.stderr}"
    return dict(json.loads(result.stdout.strip().splitlines()[-1]))


@pytest.mark.parametrize(
    "claim",
    [
        "1_snapshot_a_written",
        "2_live_is_b_not_union",
        "3_snapshot_b_written",
        "4_current_is_only_b",
        "5_trusted_counts_both",
        "5_digests_differ",
        "6_relabel_is_same_corpus",
        "6_distinct_corpora_still_two",
        "7_corrupt_snapshot_drops_out",
        "8_snapshot_a_byte_identical",
        "9_doctor_clean",
    ],
)
def test_the_installed_package_upholds(results: dict, claim: str) -> None:
    assert results[claim] is True, f"{claim} did not hold in the installed package"
