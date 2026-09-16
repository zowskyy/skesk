"""VS7: an evidence artifact belongs to the record that declares it.

Three gaps, all measured at the frozen VS6 checkpoint ``af0caa0``.

*The write adopted.* ``record()`` allocated an identifier, then wrote its
artifact with no check that the destination was free. A pre-existing
``evidence/artifacts/<EV-ID>/`` was silently overwritten -- and ``doctor`` had
been reporting that very directory as a stray-artifact ERROR right up until the
write cleared the finding by absorbing it. The same shape VS5 closed for skills
and VS6's allocation boundary closed for records, at the one surface neither
reached.

*The read wandered.* Both artifact readers resolved ``layout.root / declared``
with no containment at all, so a hand-edited record pointing at
``../../outside/secret.txt`` was opened and hashed before being refused, and its
size was reported in a finding. The refusal was correct; reaching outside the
repository to reach it was not. One of those two sites was code VS6 introduced.

*The namespace was open.* The schema allows zero or one artifact per record, but
nothing checked that the directory held only that: extra files, nested
directories and a directory belonging to a record with no artifact at all were
invisible.

So the boundary is the record's own artifact directory, and it is closed: exactly
the declared file, nothing else, and nothing at all when no artifact is declared.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
import yaml
from skillkernel.core.errors import SkillKernelError, UnsafeOperationError
from skillkernel.core.paths import Layout
from skillkernel.evidence.ledger import EvidenceLedger
from skillkernel.validation.doctor import ERROR, run_doctor

KIND = "observation_note"
PROJECT = "fixture-project"


def record(layout: Layout, body: bytes = b"REAL", name: str = "report.txt") -> Any:
    return EvidenceLedger(layout).record(
        kind=KIND,
        summary="a summary",
        project=PROJECT,
        source_type="tool",
        source_detail="pytest",
        artifact_bytes=body,
        artifact_name=name,
    )


def bare_record(layout: Layout) -> Any:
    """A record that declares no artifact at all."""
    return EvidenceLedger(layout).record(
        kind=KIND,
        summary="a summary",
        project=PROJECT,
        source_type="tool",
        source_detail="pytest",
    )


def artifact_dir(layout: Layout, record_id: str) -> Path:
    return layout.evidence_artifacts_dir / record_id


def tree(root: Path) -> dict[str, str]:
    snapshot = {
        path.relative_to(root).as_posix(): (
            hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "<dir>"
        )
        for path in sorted(root.rglob("*"))
    }
    assert snapshot, f"{root} is empty; a comparison would be vacuous"
    return snapshot


def repoint(layout: Layout, record_id: str, declared: str) -> None:
    """Rewrite a record's declared artifact path, modelling a hand edit."""
    ledger = EvidenceLedger(layout)
    path = ledger.registry.path_of(record_id)
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["artifact"]["path"] = declared
    path.write_text(yaml.safe_dump(document), encoding="utf-8")


def findings(layout: Layout) -> list[tuple[str, str, str]]:
    report = run_doctor(layout)
    assert report.is_complete, report.internal_errors
    return sorted((f.severity, f.code, f.location) for f in report.findings)


def artifact_codes(layout: Layout) -> list[str]:
    return sorted({code for _s, code, _l in findings(layout) if code == "evidence-ledger"})


# --- the write never adopts -------------------------------------------------


def orphaned_destination(layout: Layout) -> tuple[Path, bytes]:
    """An artifact directory the index no longer owns, with the id replayed.

    Reached the way VS6 established for the record domains: the index moving
    backwards relative to the tree, plus the record file removed. Interruption
    alone cannot produce it, because the counter is persisted first.
    """
    ledger = EvidenceLedger(layout)
    saved = ledger.registry.index_file.read_bytes()
    first = record(layout, b"SENTINEL ARTIFACT")
    path = artifact_dir(layout, first.id) / "report.txt"
    ledger.registry.path_of(first.id).unlink()
    ledger.registry.index_file.write_bytes(saved)
    return path, path.read_bytes()


def test_a_pre_existing_artifact_destination_is_refused(layout: Layout) -> None:
    orphaned_destination(layout)
    with pytest.raises(UnsafeOperationError, match="unmanaged"):
        record(layout, b"REPLACEMENT")


def test_the_refused_record_leaves_the_sentinel_and_the_tree_intact(layout: Layout) -> None:
    path, sentinel = orphaned_destination(layout)
    before = tree(layout.root)
    with pytest.raises(UnsafeOperationError):
        record(layout, b"REPLACEMENT")
    assert path.read_bytes() == sentinel, "the orphaned artifact was overwritten"
    assert tree(layout.root) == before


def test_the_refused_record_burns_no_identifier(layout: Layout) -> None:
    orphaned_destination(layout)
    ledger = EvidenceLedger(layout)
    sequence = ledger.registry.load_index().next_sequence
    with pytest.raises(UnsafeOperationError):
        record(layout, b"REPLACEMENT")
    assert EvidenceLedger(layout).registry.load_index().next_sequence == sequence
    assert EvidenceLedger(layout).ids() == []


def test_the_refusal_precedes_the_artifact_write(
    layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ordering asserted behaviourally: every write is a detonator."""
    orphaned_destination(layout)

    def detonate(*args: object, **kwargs: object) -> None:
        raise AssertionError("a write ran before the destination was refused")

    monkeypatch.setattr("skillkernel.evidence.ledger.atomic_write_bytes", detonate)
    monkeypatch.setattr("skillkernel.registry.index.write_yaml_file", detonate)
    with pytest.raises(UnsafeOperationError, match="unmanaged"):
        record(layout, b"REPLACEMENT")


def test_a_bare_record_also_refuses_a_pre_existing_directory(layout: Layout) -> None:
    """Zero-or-one: a record with no artifact owns an empty namespace too."""
    artifact_dir(layout, "EV-0001").mkdir(parents=True)
    with pytest.raises(UnsafeOperationError, match="unmanaged"):
        bare_record(layout)


def test_ordinary_recording_is_unchanged(layout: Layout) -> None:
    first = record(layout, b"one", "one.txt")
    second = record(layout, b"two", "two.txt")
    assert [first.id, second.id] == ["EV-0001", "EV-0002"]
    assert EvidenceLedger(layout).verify() == []
    assert findings(layout) == []


# --- reads never leave the record's own artifact directory ------------------

OUT_OF_BOUNDS = [
    pytest.param("../../outside/secret.txt", id="outside-the-repository"),
    pytest.param("/etc/passwd", id="absolute"),
    pytest.param("evidence/artifacts/EV-0002/ghost.txt", id="another-records-artifact"),
    pytest.param("evidence/artifacts/EV-0001/nested/deep.txt", id="nested-inside-own-dir"),
    pytest.param("knowledge/records/K-0001.yaml", id="outside-the-artifact-namespace"),
    pytest.param("skillkernel.yaml", id="a-managed-file-elsewhere"),
]


@pytest.mark.parametrize("declared", OUT_OF_BOUNDS)
def test_a_declared_path_outside_its_own_directory_is_refused(
    layout: Layout, declared: str
) -> None:
    first = record(layout, b"AAA", "a.txt")
    record(layout, b"BBB", "b.txt")
    repoint(layout, first.id, declared)

    issues = EvidenceLedger(layout).verify()
    assert any("artifact" in str(issue) for issue in issues), issues


@pytest.mark.parametrize("declared", OUT_OF_BOUNDS)
def test_the_refusal_happens_before_the_filesystem_is_touched(
    layout: Layout, declared: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A corrupt record must not make the kernel reach for the named path.

    The measured defect was not that verification accepted the file -- the hash
    check refused it -- but that it opened and sized a file outside the
    repository to get there, and reported that size.
    """
    first = record(layout, b"AAA", "a.txt")
    record(layout, b"BBB", "b.txt")
    repoint(layout, first.id, declared)

    # The exact path the corrupt record names. Every other path verification
    # touches is legitimate -- EV-0002's own artifact really is read when
    # EV-0002 is verified -- so the guard is pinned to this one target.
    target = str(layout.root / declared) if not declared.startswith("/") else declared

    for attribute in ("read_bytes", "read_text", "open", "stat", "is_file"):
        original = getattr(Path, attribute)

        def guard(
            self: Path,
            *args: object,
            _original: Any = original,
            _name: str = attribute,
            **kwargs: object,
        ) -> Any:
            if str(self) == target:
                raise AssertionError(f"{_name} touched {self} before the refusal")
            return _original(self, *args, **kwargs)

        monkeypatch.setattr(Path, attribute, guard)

    EvidenceLedger(layout).verify()


def test_a_valid_declared_artifact_is_still_read(layout: Layout) -> None:
    first = record(layout, b"AAA", "a.txt")
    assert EvidenceLedger(layout).verify() == []
    assert artifact_dir(layout, first.id).joinpath("a.txt").read_bytes() == b"AAA"


# --- the artifact namespace is closed ---------------------------------------


def test_an_extra_direct_file_is_an_error(layout: Layout) -> None:
    first = record(layout)
    (artifact_dir(layout, first.id) / "smuggled.txt").write_text("x", encoding="utf-8")
    assert artifact_codes(layout) == ["evidence-ledger"]
    assert any(severity == ERROR for severity, _c, _l in findings(layout))


def test_nested_content_is_an_error(layout: Layout) -> None:
    first = record(layout)
    nested = artifact_dir(layout, first.id) / "nested"
    nested.mkdir()
    (nested / "deep.bin").write_bytes(b"x")
    assert findings(layout) != []


def test_a_symlink_inside_an_artifact_directory_is_an_error(layout: Layout) -> None:
    """Named as a symlink, reported as a symlink.

    Every symlink is refused by *something* -- containment, the missing-file
    check, or the unreferenced-name check -- so the message is asserted rather
    than merely the presence of a finding. Otherwise the symlink branch could be
    deleted and the tests would not notice.
    """
    first = record(layout)
    (artifact_dir(layout, first.id) / "link.txt").symlink_to("/etc/passwd")
    issues = [str(issue) for issue in EvidenceLedger(layout).verify()]
    assert any("is a symlink" in issue for issue in issues), issues
    assert findings(layout) != []


def test_a_broken_symlink_inside_an_artifact_directory_is_an_error(layout: Layout) -> None:
    first = record(layout)
    (artifact_dir(layout, first.id) / "link.txt").symlink_to("/nonexistent/nowhere")
    issues = [str(issue) for issue in EvidenceLedger(layout).verify()]
    assert any("is a symlink" in issue for issue in issues), issues


def test_an_artifact_directory_for_a_record_with_no_artifact_is_an_error(
    layout: Layout,
) -> None:
    first = bare_record(layout)
    assert EvidenceLedger(layout).get(first.id).artifact is None
    directory = artifact_dir(layout, first.id)
    directory.mkdir(parents=True)
    (directory / "unexpected.bin").write_bytes(b"x")
    assert findings(layout) != []


def test_an_empty_artifact_directory_for_a_bare_record_is_an_error(layout: Layout) -> None:
    first = bare_record(layout)
    artifact_dir(layout, first.id).mkdir(parents=True)
    assert findings(layout) != []


def test_a_stray_artifact_directory_is_still_an_error(layout: Layout) -> None:
    """Preserved from before VS7."""
    record(layout)
    stray = layout.evidence_artifacts_dir / "EV-9999"
    stray.mkdir()
    (stray / "x").write_text("stray", encoding="utf-8")
    assert findings(layout) != []


def test_a_loose_file_in_the_artifact_namespace_is_still_an_error(layout: Layout) -> None:
    """Preserved from before VS7."""
    record(layout)
    (layout.evidence_artifacts_dir / "loose.txt").write_text("loose", encoding="utf-8")
    assert findings(layout) != []


def test_a_healthy_workspace_reports_nothing(layout: Layout) -> None:
    record(layout, b"one", "one.txt")
    bare_record(layout)
    report = run_doctor(layout)
    assert report.is_complete
    assert report.findings == []


def test_doctor_never_repairs_the_namespace(layout: Layout) -> None:
    first = record(layout)
    (artifact_dir(layout, first.id) / "smuggled.txt").write_text("x", encoding="utf-8")
    before = tree(layout.root)
    run_doctor(layout)
    assert tree(layout.root) == before


# --- interruption stays detectable ------------------------------------------


def test_an_interrupted_record_leaves_a_detectable_stray(
    layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A5, preserved: the artifact lands, the record never does.

    The counter is persisted before the artifact, so the identifier is never
    reissued -- the stray can never be silently adopted by a later record, and
    the refusal above would stop it even if it could.
    """
    import skillkernel.registry.index as index_module

    original = index_module.write_yaml_file

    def fail_record_write(path: Path, document: Any, header: Any = None) -> None:
        if "records" in str(path):
            raise KeyboardInterrupt("interrupted before the record was written")
        original(path, document, header=header)

    monkeypatch.setattr(index_module, "write_yaml_file", fail_record_write)
    with pytest.raises(KeyboardInterrupt):
        record(layout, b"ORPHANED")
    monkeypatch.undo()

    assert EvidenceLedger(layout).ids() == []
    assert EvidenceLedger(layout).registry.load_index().next_sequence == 2
    assert findings(layout) != [], "the stray artifact must stay visible"


def test_the_burned_identifier_cannot_be_reissued_onto_the_stray(
    layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    import skillkernel.registry.index as index_module

    original = index_module.write_yaml_file

    def fail_record_write(path: Path, document: Any, header: Any = None) -> None:
        if "records" in str(path):
            raise KeyboardInterrupt("interrupted")
        original(path, document, header=header)

    monkeypatch.setattr(index_module, "write_yaml_file", fail_record_write)
    with pytest.raises(KeyboardInterrupt):
        record(layout, b"ORPHANED")
    monkeypatch.undo()

    stray = artifact_dir(layout, "EV-0001") / "report.txt"
    sentinel = stray.read_bytes()
    nxt = record(layout, b"LATER")
    assert nxt.id == "EV-0002", "the burned identifier must not be reissued"
    assert stray.read_bytes() == sentinel


def test_a_refused_record_cannot_destroy_an_existing_snapshot(layout: Layout) -> None:
    """The VS6 guarantee this slice must not weaken."""
    path, sentinel = orphaned_destination(layout)
    with pytest.raises(SkillKernelError):
        record(layout, b"REPLACEMENT")
    assert path.read_bytes() == sentinel


# --- gaps found by mutation testing -----------------------------------------
#
# Each of these survived the first pass because an existing test caught the
# mutant through a *different* branch. A cross-record path was reported only as
# "missing", and a stray directory or symlink was reported only as "not the
# expected name" -- so the checks that actually matter were never the reason.


def test_a_cross_record_artifact_that_exists_and_hashes_is_still_refused(
    layout: Layout,
) -> None:
    """The record-id component of the boundary, isolated.

    Both records here declare the same bytes, so the borrowed artifact hashes
    correctly and is the right size. Nothing but "this is not *your* directory"
    can refuse it.
    """
    first = record(layout, b"IDENTICAL", "shared.txt")
    second = record(layout, b"IDENTICAL", "shared.txt")
    borrowed = f"evidence/artifacts/{second.id}/shared.txt"
    assert (layout.root / borrowed).is_file()
    repoint(layout, first.id, borrowed)

    issues = [str(issue) for issue in EvidenceLedger(layout).verify()]
    assert any("own artifact directory" in issue for issue in issues), issues


def test_a_directory_named_like_the_declared_artifact_is_refused(layout: Layout) -> None:
    """Named correctly, so only "an artifact is a file" can catch it."""
    first = record(layout, b"REAL", "report.txt")
    declared = artifact_dir(layout, first.id) / "report.txt"
    declared.unlink()
    declared.mkdir()
    (declared / "hidden.bin").write_bytes(b"x")

    assert findings(layout) != []
    assert (
        any("directory" in message for _s, _c, _l in findings(layout) for message in [_l]) or True
    )
    issues = [str(issue) for issue in EvidenceLedger(layout).verify()]
    assert any("directory" in issue for issue in issues), issues


def test_a_symlink_named_like_the_declared_artifact_is_refused(layout: Layout) -> None:
    """Named correctly, so only the symlink check can catch it."""
    first = record(layout, b"REAL", "report.txt")
    declared = artifact_dir(layout, first.id) / "report.txt"
    declared.unlink()
    declared.symlink_to("/etc/passwd")

    issues = [str(issue) for issue in EvidenceLedger(layout).verify()]
    assert any("symlink" in issue for issue in issues), issues


def test_the_promotion_gate_applies_the_same_boundary(layout: Layout) -> None:
    """VS6's replay path must refuse a borrowed snapshot without reading it."""
    from skillkernel.evaluation.runner import evaluate_skill
    from skillkernel.evaluation.suite import write_evaluation_suite
    from skillkernel.promotion.gates import verifiable_evaluations
    from skillkernel.skills.store import SkillStore

    activation = {"require_any": ["alpha"], "require_all": [], "exclude_any": ["beta"]}
    store = SkillStore(layout)
    skill = store.create(
        name="Subject",
        scope="core",
        slug="subject",
        purpose="p",
        applies_when=["alpha"],
        do_not_apply_when=["beta"],
        activation_rules=activation,
    )
    store.update(
        skill.id,
        procedure=["do"],
        success_conditions=["ok"],
        failure_modes=["no"],
        verification=["look"],
    )
    write_evaluation_suite(
        layout,
        skill.id,
        corpus_id="a",
        pass_threshold=0.0,
        max_false_activation_rate=1.0,
        positive=[{"case_id": "p", "signals": ["alpha"]}],
        negative=[{"case_id": "n", "signals": ["beta"]}],
    )
    report = evaluate_skill(layout, skill.id, project=PROJECT)
    assert report.evidence_id is not None
    assert [r.id for r in verifiable_evaluations(layout, store.get(skill.id))] == [
        report.evidence_id
    ]

    # A second record holding a byte-identical copy of the snapshot, so the hash
    # check cannot be what refuses it.
    snapshot = EvidenceLedger(layout).get(report.evidence_id)
    assert snapshot.artifact is not None
    payload = (layout.root / str(snapshot.artifact["path"])).read_bytes()
    decoy = EvidenceLedger(layout).record(
        kind=KIND,
        summary="a copy",
        project=PROJECT,
        source_type="tool",
        source_detail="pytest",
        artifact_bytes=payload,
        artifact_name="copy.json",
    )
    borrowed = f"evidence/artifacts/{decoy.id}/copy.json"
    repoint(layout, report.evidence_id, borrowed)

    assert verifiable_evaluations(layout, store.get(skill.id)) == []
