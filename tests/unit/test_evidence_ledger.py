"""Adversarial tests for the evidence ledger.

Promotion decisions will rest on this ledger, so the tests are written as
attacks: modify history, delete a record, insert one, reorder them, corrupt an
artifact. Each must be *detected* by ``verify()``.

The property under test is tamper evidence, not immutability -- an attacker who
rewrites the whole chain still produces a verifying ledger, and
``test_a_fully_recomputed_chain_still_verifies`` documents that limit honestly
rather than pretending otherwise.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from skillkernel.core.errors import UnsafeOperationError, ValidationError
from skillkernel.core.paths import Layout
from skillkernel.core.yamlio import load_yaml_file, write_yaml_file
from skillkernel.evidence.ledger import EvidenceLedger
from skillkernel.evidence.model import chain_hash


def add(ledger: EvidenceLedger, summary: str = "a run", **kwargs: object) -> object:
    defaults: dict[str, object] = {
        "kind": "command_output",
        "summary": summary,
        "project": "demo",
        "source_type": "command",
        "source_detail": "pytest -q",
    }
    defaults.update(kwargs)
    return ledger.record(**defaults)  # type: ignore[arg-type]


def problems(ledger: EvidenceLedger) -> str:
    return "\n".join(str(finding) for finding in ledger.verify())


# --- happy path ------------------------------------------------------------


def test_the_first_record_has_no_predecessor(ledger: EvidenceLedger) -> None:
    record = ledger.record(
        kind="command_output",
        summary="first",
        project="demo",
        source_type="command",
        source_detail="pytest",
    )
    assert record.chain["previous"] is None
    assert record.chain["previous_hash"] is None
    assert len(record.chain["hash"]) == 64


def test_subsequent_records_chain_to_their_predecessor(ledger: EvidenceLedger) -> None:
    first = ledger.record(
        kind="command_output", summary="a", project="demo", source_type="c", source_detail="d"
    )
    second = ledger.record(
        kind="command_output", summary="b", project="demo", source_type="c", source_detail="d"
    )
    assert second.chain["previous"] == first.id
    assert second.chain["previous_hash"] == first.chain["hash"]


def test_an_untouched_ledger_verifies_clean(ledger: EvidenceLedger) -> None:
    for index in range(4):
        add(ledger, f"run {index}")
    assert ledger.verify() == []


def test_an_empty_ledger_verifies_clean(ledger: EvidenceLedger) -> None:
    assert ledger.verify() == []


def test_identical_content_produces_a_different_hash_at_a_different_position(
    ledger: EvidenceLedger,
) -> None:
    """Position is part of the hash, so records cannot be swapped undetected."""
    first = add(ledger, "identical")
    second = add(ledger, "identical")
    assert first.chain["hash"] != second.chain["hash"]  # type: ignore[attr-defined]


# --- tampering with history ------------------------------------------------


def test_modifying_a_historical_record_is_detected(ledger: EvidenceLedger) -> None:
    add(ledger, "one")
    add(ledger, "two")
    add(ledger, "three")

    path = ledger.registry.path_of("EV-0002")
    document = load_yaml_file(path)
    document["summary"] = "silently rewritten"
    write_yaml_file(path, document)

    assert "EV-0002: chain.hash does not match" in problems(ledger)


def test_modifying_a_nested_link_is_detected(ledger: EvidenceLedger) -> None:
    add(ledger, "one")
    path = ledger.registry.path_of("EV-0001")
    document = load_yaml_file(path)
    document["links"]["skill"] = "SKILL-0099"
    write_yaml_file(path, document)

    assert "chain.hash does not match" in problems(ledger)


def test_deleting_a_record_from_the_middle_is_detected(ledger: EvidenceLedger) -> None:
    add(ledger, "one")
    add(ledger, "two")
    add(ledger, "three")

    index = ledger.registry.load_index()
    del index.entries["EV-0002"]
    write_yaml_file(ledger.registry.index_file, index.to_document())

    report = problems(ledger)
    assert "EV-0003" in report
    assert "chain.previous" in report


def test_deleting_the_record_file_alone_is_detected(ledger: EvidenceLedger) -> None:
    add(ledger, "one")
    ledger.registry.path_of("EV-0001").unlink()
    assert "could not be loaded" in problems(ledger)


def test_inserting_a_forged_record_is_detected(ledger: EvidenceLedger) -> None:
    add(ledger, "one")
    add(ledger, "two")

    forged = dict(load_yaml_file(ledger.registry.path_of("EV-0002")))
    forged["id"] = "EV-0003"
    forged["summary"] = "forged"
    ledger.registry.put("EV-0003", forged)

    report = problems(ledger)
    assert "EV-0003" in report


def test_reordering_records_is_detected(ledger: EvidenceLedger) -> None:
    add(ledger, "one")
    add(ledger, "two")

    first_path = ledger.registry.path_of("EV-0001")
    second_path = ledger.registry.path_of("EV-0002")
    first = load_yaml_file(first_path)
    second = load_yaml_file(second_path)
    first["id"], second["id"] = "EV-0002", "EV-0001"
    write_yaml_file(first_path, second)
    write_yaml_file(second_path, first)

    assert problems(ledger) != ""


def test_a_broken_previous_hash_is_detected(ledger: EvidenceLedger) -> None:
    add(ledger, "one")
    add(ledger, "two")
    path = ledger.registry.path_of("EV-0002")
    document = load_yaml_file(path)
    document["chain"]["previous_hash"] = "0" * 64
    write_yaml_file(path, document)

    assert "previous_hash does not match" in problems(ledger)


def test_a_repointed_predecessor_is_detected(ledger: EvidenceLedger) -> None:
    add(ledger, "one")
    add(ledger, "two")
    add(ledger, "three")
    path = ledger.registry.path_of("EV-0003")
    document = load_yaml_file(path)
    document["chain"]["previous"] = "EV-0001"
    write_yaml_file(path, document)

    assert "chain.previous is 'EV-0001'" in problems(ledger)


def test_a_fully_recomputed_chain_still_verifies(ledger: EvidenceLedger) -> None:
    """The honest limit of the guarantee: this is tamper evidence, not immutability.

    An actor who can write to the repository *and* recomputes every subsequent
    hash produces a ledger that verifies. Detecting that requires an authority
    outside the repository (signed commits, an append-only remote).
    """
    add(ledger, "one")
    add(ledger, "two")

    path = ledger.registry.path_of("EV-0001")
    document = load_yaml_file(path)
    document["summary"] = "rewritten history"
    document["chain"]["hash"] = chain_hash(document, None)
    write_yaml_file(path, document)

    successor_path = ledger.registry.path_of("EV-0002")
    successor = load_yaml_file(successor_path)
    successor["chain"]["previous_hash"] = document["chain"]["hash"]
    successor["chain"]["hash"] = chain_hash(successor, document["chain"]["hash"])
    write_yaml_file(successor_path, successor)

    assert ledger.verify() == []


# --- artifacts -------------------------------------------------------------


def test_an_artifact_is_copied_hashed_and_sized(ledger: EvidenceLedger, tmp_path: Path) -> None:
    source = tmp_path / "report.txt"
    source.write_text("measurements")
    record = ledger.record(
        kind="file",
        summary="a report",
        project="demo",
        source_type="file",
        source_detail=str(source),
        artifact_path=source,
        media_type="text/plain",
    )
    assert record.artifact is not None
    assert record.artifact["bytes"] == len("measurements")
    assert record.artifact["path"] == f"evidence/artifacts/{record.id}/report.txt"
    assert ledger.verify() == []


def test_a_corrupted_artifact_is_detected(ledger: EvidenceLedger, layout: Layout) -> None:
    record = ledger.record(
        kind="file",
        summary="a report",
        project="demo",
        source_type="file",
        source_detail="generated",
        artifact_bytes=b"original measurements",
        artifact_name="report.txt",
    )
    assert record.artifact is not None
    (layout.root / str(record.artifact["path"])).write_text("tampered measurements!")
    assert "does not match its recorded hash" in problems(ledger)


def test_a_missing_artifact_is_detected(ledger: EvidenceLedger, layout: Layout) -> None:
    record = ledger.record(
        kind="file",
        summary="a report",
        project="demo",
        source_type="file",
        source_detail="generated",
        artifact_bytes=b"data",
        artifact_name="report.txt",
    )
    assert record.artifact is not None
    (layout.root / str(record.artifact["path"])).unlink()
    assert "is missing" in problems(ledger)


def test_a_stray_artifact_directory_is_detected(ledger: EvidenceLedger, layout: Layout) -> None:
    stray = layout.evidence_artifacts_dir / "EV-9999"
    stray.mkdir(parents=True)
    (stray / "planted.txt").write_text("x")
    assert "has no matching evidence record" in problems(ledger)


def test_a_record_may_legitimately_have_no_artifact(ledger: EvidenceLedger) -> None:
    record = ledger.record(
        kind="external_report",
        summary="a report we do not hold",
        project="demo",
        source_type="external",
        source_detail="vendor bulletin 4471",
    )
    assert record.artifact is None
    assert ledger.verify() == []


def test_supplying_both_an_artifact_path_and_bytes_is_rejected(
    ledger: EvidenceLedger, tmp_path: Path
) -> None:
    source = tmp_path / "f.txt"
    source.write_text("x")
    with pytest.raises(ValidationError, match="not both"):
        ledger.record(
            kind="file",
            summary="s",
            project="demo",
            source_type="t",
            source_detail="d",
            artifact_path=source,
            artifact_bytes=b"x",
        )


def test_raw_bytes_require_an_artifact_name(ledger: EvidenceLedger) -> None:
    with pytest.raises(ValidationError, match="artifact_name is required"):
        ledger.record(
            kind="file",
            summary="s",
            project="demo",
            source_type="t",
            source_detail="d",
            artifact_bytes=b"x",
        )


def test_a_missing_source_file_is_rejected(ledger: EvidenceLedger, tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="does not exist"):
        ledger.record(
            kind="file",
            summary="s",
            project="demo",
            source_type="t",
            source_detail="d",
            artifact_path=tmp_path / "absent.txt",
        )


def test_an_artifact_name_cannot_escape_the_artifacts_directory(
    ledger: EvidenceLedger, layout: Layout
) -> None:
    record = ledger.record(
        kind="file",
        summary="s",
        project="demo",
        source_type="t",
        source_detail="d",
        artifact_bytes=b"x",
        artifact_name="../../../../etc/passwd",
    )
    assert record.artifact is not None
    stored = layout.root / str(record.artifact["path"])
    assert layout.evidence_artifacts_dir in stored.parents
    assert stored.name == "passwd"


@pytest.mark.parametrize("name", ["", ".", ".."])
def test_a_degenerate_artifact_name_is_refused(ledger: EvidenceLedger, name: str) -> None:
    with pytest.raises((UnsafeOperationError, ValidationError)):
        ledger.record(
            kind="file",
            summary="s",
            project="demo",
            source_type="t",
            source_detail="d",
            artifact_bytes=b"x",
            artifact_name=name,
        )


# --- queries ---------------------------------------------------------------


def test_records_are_listed_in_allocation_order_not_lexicographic(
    ledger: EvidenceLedger,
) -> None:
    for _ in range(11):
        add(ledger)
    assert ledger.ids()[:2] == ["EV-0001", "EV-0002"]
    assert ledger.ids()[-1] == "EV-0011"


def test_for_skill_filters_to_one_skill(ledger: EvidenceLedger) -> None:
    add(ledger, "unrelated")
    add(ledger, "relevant", skill="SKILL-0001")
    assert [record.id for record in ledger.for_skill("SKILL-0001")] == ["EV-0002"]
