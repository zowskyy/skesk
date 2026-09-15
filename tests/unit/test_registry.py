"""Registry invariants: identifier allocation, write ordering, corruption detection."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from skillkernel.core.errors import (
    DuplicateIdError,
    IntegrityError,
    RecordNotFoundError,
    UnsafeOperationError,
    ValidationError,
)
from skillkernel.core.ids import KNOWLEDGE
from skillkernel.core.paths import Layout
from skillkernel.core.yamlio import load_yaml_file, write_yaml_file
from skillkernel.registry import Registry


@pytest.fixture
def registry(layout: Layout) -> Registry:
    built = Registry(layout, kind="knowledge", domain_dir=layout.knowledge_dir, id_prefix=KNOWLEDGE)
    built.create()
    return built


def doc(record_id: str, **extra: Any) -> dict[str, Any]:
    return {"id": record_id, "body": "content", **extra}


# --- creation --------------------------------------------------------------


def test_create_is_idempotent_and_does_not_reset_state(registry: Registry) -> None:
    first = registry.allocate_id()
    registry.create()
    assert registry.allocate_id() != first
    assert registry.load_index().next_sequence == 3


# --- identifier allocation -------------------------------------------------


def test_allocation_is_monotonic(registry: Registry) -> None:
    assert [registry.allocate_id() for _ in range(3)] == ["K-0001", "K-0002", "K-0003"]


def test_allocation_persists_across_a_fresh_registry_object(
    layout: Layout, registry: Registry
) -> None:
    registry.allocate_id()
    registry.allocate_id()
    reopened = Registry(
        layout, kind="knowledge", domain_dir=layout.knowledge_dir, id_prefix=KNOWLEDGE
    )
    assert reopened.allocate_id() == "K-0003"


def test_an_allocated_but_unused_identifier_is_burned_never_reissued(
    registry: Registry,
) -> None:
    """A crash between allocation and write must lose a number, not reuse one."""
    burned = registry.allocate_id()
    assert registry.allocate_id() != burned
    assert not registry.has(burned)


def test_removing_a_record_from_the_index_does_not_free_its_identifier(
    registry: Registry,
) -> None:
    first = registry.allocate_id()
    registry.put(first, doc(first))
    index = registry.load_index()
    del index.entries[first]
    write_yaml_file(registry.index_file, index.to_document())

    assert registry.allocate_id() == "K-0002"


def test_a_counter_that_would_reuse_an_existing_identifier_is_rejected(
    registry: Registry,
) -> None:
    record_id = registry.allocate_id()
    registry.put(record_id, doc(record_id))
    document = load_yaml_file(registry.index_file)
    document["next_sequence"] = 1
    write_yaml_file(registry.index_file, document)

    with pytest.raises(IntegrityError, match="would reuse"):
        registry.load_index()


def test_putting_a_record_pushes_the_counter_past_it(registry: Registry) -> None:
    registry.put("K-0050", doc("K-0050"))
    assert registry.load_index().next_sequence == 51


# --- storing and loading ---------------------------------------------------


def test_put_then_load_round_trips(registry: Registry) -> None:
    record_id = registry.allocate_id()
    registry.put(record_id, doc(record_id, extra="value"))
    assert registry.load(record_id)["extra"] == "value"


def test_put_writes_to_the_conventional_path(registry: Registry, layout: Layout) -> None:
    registry.put("K-0001", doc("K-0001"))
    assert (layout.knowledge_dir / "records" / "K-0001.yaml").is_file()


def test_put_rejects_a_document_whose_id_disagrees_with_its_slot(registry: Registry) -> None:
    with pytest.raises(ValidationError, match="declares id"):
        registry.put("K-0001", doc("K-0002"))


def test_expect_new_rejects_an_existing_identifier(registry: Registry) -> None:
    registry.put("K-0001", doc("K-0001"), expect_new=True)
    with pytest.raises(DuplicateIdError, match="already exists"):
        registry.put("K-0001", doc("K-0001"), expect_new=True)


def test_put_without_expect_new_updates_in_place(registry: Registry) -> None:
    registry.put("K-0001", doc("K-0001", body="first"))
    registry.put("K-0001", doc("K-0001", body="second"))
    assert registry.load("K-0001")["body"] == "second"
    assert len(registry.ids()) == 1


def test_loading_an_unregistered_identifier_raises(registry: Registry) -> None:
    with pytest.raises(RecordNotFoundError, match="not registered"):
        registry.load("K-0404")


def test_summary_is_denormalized_into_the_index(registry: Registry) -> None:
    registry.put("K-0001", doc("K-0001"), summary={"status": "proposed"})
    assert registry.entry("K-0001").summary == {"status": "proposed"}


def test_ids_are_returned_sorted(registry: Registry) -> None:
    for record_id in ["K-0003", "K-0001", "K-0002"]:
        registry.put(record_id, doc(record_id))
    assert registry.ids() == ["K-0001", "K-0002", "K-0003"]


# --- write ordering --------------------------------------------------------


def test_the_record_file_is_written_before_the_index(
    registry: Registry, layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An interruption may orphan a file; it must never dangle an index entry."""
    record_id = registry.allocate_id()

    def fail_index_write(self: Registry, index: object) -> None:
        raise OSError("simulated interruption after the record was written")

    monkeypatch.setattr(Registry, "_write_index", fail_index_write)
    with pytest.raises(OSError, match="simulated interruption"):
        registry.put(record_id, doc(record_id))

    monkeypatch.undo()
    assert (layout.knowledge_dir / "records" / f"{record_id}.yaml").is_file()
    assert record_id not in registry.ids()
    assert registry.orphan_record_files() == [
        layout.knowledge_dir / "records" / f"{record_id}.yaml"
    ]


# --- corruption detection --------------------------------------------------


def test_an_orphan_record_file_is_detected(registry: Registry, layout: Layout) -> None:
    assert registry.orphan_record_files() == []
    stray = layout.knowledge_dir / "records" / "K-9999.yaml"
    write_yaml_file(stray, {"id": "K-9999"})
    assert registry.orphan_record_files() == [stray]


def test_a_dangling_index_entry_is_detected_on_load(registry: Registry) -> None:
    registry.put("K-0001", doc("K-0001"))
    registry.path_of("K-0001").unlink()
    with pytest.raises(ValidationError, match="does not exist"):
        registry.load("K-0001")


def test_a_record_whose_file_disagrees_with_its_registration_is_rejected(
    registry: Registry,
) -> None:
    registry.put("K-0001", doc("K-0001"))
    write_yaml_file(registry.path_of("K-0001"), {"id": "K-0002", "body": "swapped"})
    with pytest.raises(IntegrityError, match="declares id"):
        registry.load("K-0001")


def test_a_record_file_that_is_not_a_mapping_is_rejected(registry: Registry) -> None:
    registry.put("K-0001", doc("K-0001"))
    registry.path_of("K-0001").write_text("- just\n- a list\n")
    with pytest.raises(ValidationError):
        registry.load("K-0001")


def test_duplicate_identifiers_in_the_index_are_rejected(registry: Registry) -> None:
    registry.put("K-0001", doc("K-0001"))
    document = load_yaml_file(registry.index_file)
    document["entries"] = [*document["entries"], dict(document["entries"][0])]
    write_yaml_file(registry.index_file, document)
    with pytest.raises(DuplicateIdError, match="more than once"):
        registry.load_index()


def test_an_index_declaring_the_wrong_kind_is_rejected(registry: Registry) -> None:
    document = load_yaml_file(registry.index_file)
    document["kind"] = "experiments"
    write_yaml_file(registry.index_file, document)
    with pytest.raises(IntegrityError, match="declares kind"):
        registry.load_index()


def test_an_index_entry_with_a_foreign_prefix_is_rejected(registry: Registry) -> None:
    document = load_yaml_file(registry.index_file)
    document["entries"] = [{"id": "EXP-0001", "path": "records/EXP-0001.yaml"}]
    document["next_sequence"] = 2
    write_yaml_file(registry.index_file, document)
    with pytest.raises(ValidationError, match="expected a K-"):
        registry.load_index()


def test_a_structurally_invalid_index_is_rejected(registry: Registry) -> None:
    write_yaml_file(registry.index_file, {"schema_version": 1, "kind": "knowledge"})
    with pytest.raises(ValidationError):
        registry.load_index()


def test_an_index_path_escaping_the_repository_is_refused(
    registry: Registry, layout: Layout
) -> None:
    document = load_yaml_file(registry.index_file)
    document["entries"] = [{"id": "K-0001", "path": "../../../../etc/passwd"}]
    document["next_sequence"] = 2
    write_yaml_file(registry.index_file, document)
    with pytest.raises(UnsafeOperationError, match="outside the SkillKernel repository"):
        registry.path_of("K-0001")


def test_put_refuses_a_relative_path_that_escapes_the_repository(registry: Registry) -> None:
    with pytest.raises(UnsafeOperationError):
        registry.put("K-0001", doc("K-0001"), relative_path="../../escaped.yaml")


def test_load_all_yields_every_registered_record(registry: Registry) -> None:
    for record_id in ["K-0001", "K-0002"]:
        registry.put(record_id, doc(record_id))
    assert [record_id for record_id, _ in registry.load_all()] == ["K-0001", "K-0002"]


def test_orphan_detection_tolerates_a_missing_records_directory(
    layout: Layout, tmp_path: Path
) -> None:
    empty = Registry(layout, kind="knowledge", domain_dir=tmp_path / "nowhere", id_prefix=KNOWLEDGE)
    assert empty.orphan_record_files() == []
