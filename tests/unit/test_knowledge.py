"""Knowledge status transitions and lineage invariants.

Nothing is ever deleted: a claim that turns out to be wrong keeps its
identifier and becomes ``refuted``, so every skill that cited it stays
traceable.
"""

from __future__ import annotations

import pytest
from skillkernel.core.errors import IntegrityError, ValidationError
from skillkernel.core.yamlio import load_yaml_file, write_yaml_file
from skillkernel.knowledge.store import KnowledgeStore


def add(store: KnowledgeStore, statement: str = "A claim about reality.") -> object:
    return store.add(statement=statement, scope="project:demo", source_type="research")


def test_a_new_record_starts_proposed(knowledge: KnowledgeStore) -> None:
    record = knowledge.add(
        statement="Deltas accumulate.", scope="universal", source_type="research"
    )
    assert record.status == "proposed"
    assert record.is_usable


def test_a_record_can_be_supported(knowledge: KnowledgeStore) -> None:
    record = add(knowledge)
    assert knowledge.set_status(record.id, "supported").status == "supported"  # type: ignore[attr-defined]


def test_reaching_refuted_through_set_status_is_refused(knowledge: KnowledgeStore) -> None:
    record = add(knowledge)
    with pytest.raises(ValidationError, match="use refute"):
        knowledge.set_status(record.id, "refuted")  # type: ignore[attr-defined]


def test_a_refutation_must_state_a_reason(knowledge: KnowledgeStore) -> None:
    record = add(knowledge)
    with pytest.raises(ValidationError, match="must state a reason"):
        knowledge.refute(record.id, reason="   ")  # type: ignore[attr-defined]


def test_a_refuted_record_survives_with_its_identifier_and_reason(
    knowledge: KnowledgeStore,
) -> None:
    record = add(knowledge, "Frame time is constant.")
    refuted = knowledge.refute(record.id, reason="Measured 8ms of jitter under load.")  # type: ignore[attr-defined]

    assert refuted.status == "refuted"
    assert refuted.statement == "Frame time is constant."
    assert refuted.refutation is not None
    assert "jitter" in refuted.refutation["reason"]
    assert not refuted.is_usable
    assert knowledge.has(record.id)  # type: ignore[attr-defined]


def test_a_refuted_record_cannot_be_quietly_restored(knowledge: KnowledgeStore) -> None:
    record = add(knowledge)
    knowledge.refute(record.id, reason="disproved")  # type: ignore[attr-defined]
    with pytest.raises(ValidationError, match="cannot be returned"):
        knowledge.set_status(record.id, "supported")  # type: ignore[attr-defined]


def test_refuted_ids_reports_the_refuted_set(knowledge: KnowledgeStore) -> None:
    kept = add(knowledge, "kept")
    dropped = add(knowledge, "dropped")
    knowledge.refute(dropped.id, reason="disproved")  # type: ignore[attr-defined]
    assert knowledge.refuted_ids() == {dropped.id}  # type: ignore[attr-defined]
    assert kept.id not in knowledge.refuted_ids()  # type: ignore[attr-defined]


# --- supersession ----------------------------------------------------------


def test_supersession_writes_both_halves_of_the_link(knowledge: KnowledgeStore) -> None:
    old = add(knowledge, "The old understanding.")
    new = add(knowledge, "The corrected understanding.")
    updated_old, updated_new = knowledge.supersede(old.id, new.id)  # type: ignore[attr-defined]

    assert updated_old.status == "superseded"
    assert updated_old.superseded_by == new.id  # type: ignore[attr-defined]
    assert old.id in updated_new.supersedes  # type: ignore[attr-defined]
    assert knowledge.lineage_issues() == []


def test_a_record_cannot_supersede_itself(knowledge: KnowledgeStore) -> None:
    record = add(knowledge)
    with pytest.raises(ValidationError, match="cannot supersede itself"):
        knowledge.supersede(record.id, record.id)  # type: ignore[attr-defined]


def test_resuperseding_to_a_different_successor_is_refused(knowledge: KnowledgeStore) -> None:
    old = add(knowledge, "old")
    first = add(knowledge, "first successor")
    second = add(knowledge, "second successor")
    knowledge.supersede(old.id, first.id)  # type: ignore[attr-defined]
    with pytest.raises(IntegrityError, match="already superseded"):
        knowledge.supersede(old.id, second.id)  # type: ignore[attr-defined]


def test_a_one_sided_successor_link_is_detected(knowledge: KnowledgeStore) -> None:
    old = add(knowledge, "old")
    new = add(knowledge, "new")
    path = knowledge.registry.path_of(old.id)  # type: ignore[attr-defined]
    document = load_yaml_file(path)
    document["status"] = "superseded"
    document["superseded_by"] = new.id  # type: ignore[attr-defined]
    write_yaml_file(path, document)

    issues = knowledge.lineage_issues()
    assert any("does not list" in issue for issue in issues)


def test_a_successor_that_does_not_exist_is_detected(knowledge: KnowledgeStore) -> None:
    record = add(knowledge)
    path = knowledge.registry.path_of(record.id)  # type: ignore[attr-defined]
    document = load_yaml_file(path)
    document["status"] = "superseded"
    document["superseded_by"] = "K-9999"
    write_yaml_file(path, document)

    assert any("does not exist" in issue for issue in knowledge.lineage_issues())


def test_a_successor_link_without_the_superseded_status_is_detected(
    knowledge: KnowledgeStore,
) -> None:
    old = add(knowledge, "old")
    new = add(knowledge, "new")
    knowledge.supersede(old.id, new.id)  # type: ignore[attr-defined]

    path = knowledge.registry.path_of(old.id)  # type: ignore[attr-defined]
    document = load_yaml_file(path)
    document["status"] = "supported"
    write_yaml_file(path, document)

    assert any("expected 'superseded'" in issue for issue in knowledge.lineage_issues())


def test_a_refuted_record_without_a_reason_is_detected(knowledge: KnowledgeStore) -> None:
    record = add(knowledge)
    path = knowledge.registry.path_of(record.id)  # type: ignore[attr-defined]
    document = load_yaml_file(path)
    document["status"] = "refuted"
    write_yaml_file(path, document)

    assert any("no refutation reason" in issue for issue in knowledge.lineage_issues())


def test_a_dangling_supersedes_claim_is_detected(knowledge: KnowledgeStore) -> None:
    record = add(knowledge)
    path = knowledge.registry.path_of(record.id)  # type: ignore[attr-defined]
    document = load_yaml_file(path)
    document["supersedes"] = ["K-9999"]
    write_yaml_file(path, document)

    assert any("which does not exist" in issue for issue in knowledge.lineage_issues())


def test_a_clean_store_reports_no_lineage_issues(knowledge: KnowledgeStore) -> None:
    add(knowledge, "one")
    add(knowledge, "two")
    assert knowledge.lineage_issues() == []


# --- evidence attachment ---------------------------------------------------


def test_attaching_evidence_deduplicates_and_sorts(knowledge: KnowledgeStore) -> None:
    record = add(knowledge)
    knowledge.attach_evidence(record.id, ("EV-0002", "EV-0001"))  # type: ignore[attr-defined]
    updated = knowledge.attach_evidence(record.id, ("EV-0001", "EV-0003"))  # type: ignore[attr-defined]
    assert updated.evidence == ("EV-0001", "EV-0002", "EV-0003")


def test_a_malformed_evidence_reference_is_rejected(knowledge: KnowledgeStore) -> None:
    record = add(knowledge)
    with pytest.raises(ValidationError):
        knowledge.attach_evidence(record.id, ("not-an-id",))  # type: ignore[attr-defined]
