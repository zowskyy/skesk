"""Vertical Slice 8: a suite is what its definition names, proved from the wheel.

Until VS8 an evaluation suite was whatever the examples directories held, so a
planted file was scored, and replacing a corpus had no commit point: an
interrupted authoring pass left readers seeing the union of two corpora, or the
new cases wearing the old corpus's label. These prove manifest authority and the
commit hold for what actually ships.
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
    from skillkernel.evaluation import suite as sm
    from skillkernel.evaluation.suite import (
        corpus_content_digest, evaluation_input_digest, load_evaluation_suite,
        read_evaluation_inputs, upgrade_definition_to_manifest, write_evaluation_suite,
    )
    from skillkernel.evaluation.runner import evaluate_skill
    from skillkernel.promotion.gates import passing_evaluations, verifiable_evaluations
    from skillkernel.validation.doctor import run_doctor
    from skillkernel.core.errors import SkillKernelError
    import yaml

    ACT = {"require_any": ["alpha"], "require_all": [], "exclude_any": ["beta"]}
    A = dict(cid="corpus-a", pos=[{"case_id": "a-one", "signals": ["alpha"]}],
             neg=[{"case_id": "a-neg", "signals": ["beta"]}])
    B = dict(cid="corpus-b", pos=[{"case_id": "b-one", "signals": ["alpha"]}],
             neg=[{"case_id": "b-neg", "signals": ["beta"]}])
    base = Path(sys.argv[1])
    out = {}

    def ws(tag):
        L = initialize(base / tag / "ws", project_name="acceptance", now="2024-01-31T12:00:00Z")
        s = SkillStore(L)
        r = s.create(name="S", scope="core", slug="s", purpose="p", applies_when=["alpha"],
                     do_not_apply_when=["beta"], activation_rules=ACT)
        s.update(r.id, procedure=["do"], success_conditions=["ok"], failure_modes=["no"],
                 verification=["look"])
        return L, r.id
    def author(L, sid, c):
        return write_evaluation_suite(L, sid, corpus_id=c["cid"], pass_threshold=0.0,
            max_false_activation_rate=1.0, positive=c["pos"], negative=c["neg"])
    def ex(L):
        return L.skills_dir / "core" / "s" / "examples"
    def definition(L):
        return L.skills_dir / "core" / "s" / "scorer" / "eval.yaml"
    def doc(L):
        return yaml.safe_load(definition(L).read_text(encoding="utf-8"))
    def sees(L, sid):
        su = load_evaluation_suite(L, sid)
        return su.corpus_id, sorted(c.case_id for c in su.cases)
    def findings(L):
        return [(f.severity, f.code, f.location) for f in run_doctor(L).sorted_findings()]
    CASE = {"schema_version": 1, "case_id": "planted", "expected": "applies",
            "signals": ["alpha"], "description": None}

    class Boom(Exception):
        pass

    def detonate(L, sid, corpus, *, before=None, after=None):
        real = sm.write_yaml_file
        def writer(path, data, **kw):
            name = str(path)
            if before is not None and name.endswith(before):
                raise Boom(name)
            real(Path(path), data, **kw)
            if after is not None and name.endswith(after):
                raise Boom(name)
        sm.write_yaml_file = writer
        try:
            author(L, sid, corpus)
        except Boom:
            pass
        finally:
            sm.write_yaml_file = real

    # 1 -- the definition names its cases
    L, sid = ws("manifest"); author(L, sid, A)
    d = doc(L)
    out["1_definition_is_v2"] = d["schema_version"] == 2
    out["1_manifest_names_the_cases"] = d["cases"] == [
        "examples/positive/a-one.yaml", "examples/negative/a-neg.yaml"]

    # 2 -- presence is not participation
    (ex(L) / "positive" / "planted.yaml").write_text(yaml.safe_dump(CASE), encoding="utf-8")
    out["2_unnamed_file_is_not_loaded"] = sees(L, sid) == ("corpus-a", ["a-neg", "a-one"])
    out["2_unnamed_file_is_a_warning"] = findings(L) == [
        ("WARNING", "evaluation-cases:unmanifested",
         "skills/core/s/examples/positive/planted.yaml")]
    (ex(L) / "positive" / "NOTES.md").write_text("notes", encoding="utf-8")
    out["2_notes_are_silent"] = len(findings(L)) == 1

    # 3 -- a named case that is gone is a refusal, not a smaller corpus
    L, sid = ws("missing"); author(L, sid, A)
    (ex(L) / "positive" / "a-one.yaml").unlink()
    try:
        load_evaluation_suite(L, sid); out["3_missing_named_case_fails_closed"] = False
    except SkillKernelError:
        out["3_missing_named_case_fails_closed"] = True

    # 4 -- a manifest entry cannot leave its directory
    L, sid = ws("escape"); author(L, sid, A)
    d = doc(L); d["cases"] = ["examples/positive/../../../../etc/passwd"]
    definition(L).write_text(yaml.safe_dump(d), encoding="utf-8")
    try:
        load_evaluation_suite(L, sid); out["4_escaping_entry_refused"] = False
    except SkillKernelError:
        out["4_escaping_entry_refused"] = True

    # 5 -- upgrading a legacy suite is invisible
    L, sid = ws("legacy"); author(L, sid, A)
    d = doc(L); d.pop("cases"); d["schema_version"] = 1
    definition(L).write_text(yaml.safe_dump(d), encoding="utf-8")
    before_cases = sees(L, sid)
    before_input = evaluation_input_digest(L, sid)
    before_corpus = corpus_content_digest(read_evaluation_inputs(L, sid))
    e = evaluate_skill(L, sid, project="acceptance")
    out["5_legacy_suite_still_loads"] = before_cases == ("corpus-a", ["a-neg", "a-one"])
    upgraded = upgrade_definition_to_manifest(L, sid)
    out["5_upgrade_happened"] = upgraded is True and doc(L)["schema_version"] == 2
    out["5_corpus_unchanged"] = sees(L, sid) == before_cases
    out["5_input_digest_unchanged"] = evaluation_input_digest(L, sid) == before_input
    out["5_corpus_digest_unchanged"] = (
        corpus_content_digest(read_evaluation_inputs(L, sid)) == before_corpus)
    out["5_evidence_still_current"] = [x.id for x in
        passing_evaluations(L, SkillStore(L).get(sid))] == [e.evidence_id]
    out["5_upgrade_is_idempotent"] = upgrade_definition_to_manifest(L, sid) is False

    # 6 -- before the commit a reader sees exactly A
    L, sid = ws("w3"); author(L, sid, A)
    detonate(L, sid, B, before="scorer/eval.yaml")
    on_disk = sorted(p.stem for p in ex(L).rglob("*.yaml"))
    out["6_both_corpora_are_on_disk"] = on_disk == ["a-neg", "a-one", "b-neg", "b-one"]
    out["6_reader_still_sees_only_a"] = sees(L, sid) == ("corpus-a", ["a-neg", "a-one"])
    author(L, sid, B)
    out["6_rerun_converges_on_b"] = sees(L, sid) == ("corpus-b", ["b-neg", "b-one"])
    out["6_rerun_is_clean"] = findings(L) == []

    # 7 -- after the commit a reader sees exactly B, never B under A's label
    L, sid = ws("w4"); author(L, sid, A)
    detonate(L, sid, B, after="scorer/eval.yaml")
    out["7_reader_sees_exactly_b"] = sees(L, sid) == ("corpus-b", ["b-neg", "b-one"])
    out["7_leftovers_are_warnings"] = sorted(f[2] for f in findings(L)) == [
        "skills/core/s/examples/negative/a-neg.yaml",
        "skills/core/s/examples/positive/a-one.yaml"]
    out["7_leftovers_are_never_errors"] = all(f[0] == "WARNING" for f in findings(L))

    # 8 -- VS6 and VS7 semantics survive
    L, sid = ws("vs6"); author(L, sid, A)
    e = evaluate_skill(L, sid, project="acceptance")
    out["8_snapshot_still_verifies"] = [x.id for x in
        verifiable_evaluations(L, SkillStore(L).get(sid))] == [e.evidence_id]
    out["8_clean_workspace_clean_doctor"] = run_doctor(L).findings == []

    print(json.dumps(out))
    """
)


@pytest.fixture(scope="module")
def results(installed: Path, outside: Path, tmp_path_factory: pytest.TempPathFactory) -> dict:
    workspace = tmp_path_factory.mktemp("slice8")
    script = workspace / "driver.py"
    script.write_text(DRIVER, encoding="utf-8")
    result = run(str(installed / "bin" / "python"), str(script), str(workspace), cwd=outside)
    assert result.returncode == 0, f"the installed driver failed:\n{result.stdout}\n{result.stderr}"
    return dict(json.loads(result.stdout.strip().splitlines()[-1]))


@pytest.mark.parametrize(
    "claim",
    [
        "1_definition_is_v2",
        "1_manifest_names_the_cases",
        "2_unnamed_file_is_not_loaded",
        "2_unnamed_file_is_a_warning",
        "2_notes_are_silent",
        "3_missing_named_case_fails_closed",
        "4_escaping_entry_refused",
        "5_legacy_suite_still_loads",
        "5_upgrade_happened",
        "5_corpus_unchanged",
        "5_input_digest_unchanged",
        "5_corpus_digest_unchanged",
        "5_evidence_still_current",
        "5_upgrade_is_idempotent",
        "6_both_corpora_are_on_disk",
        "6_reader_still_sees_only_a",
        "6_rerun_converges_on_b",
        "6_rerun_is_clean",
        "7_reader_sees_exactly_b",
        "7_leftovers_are_warnings",
        "7_leftovers_are_never_errors",
        "8_snapshot_still_verifies",
        "8_clean_workspace_clean_doctor",
    ],
)
def test_the_installed_package_upholds(results: dict, claim: str) -> None:
    assert results[claim] is True, f"{claim} did not hold in the installed package"
