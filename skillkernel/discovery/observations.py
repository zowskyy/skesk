"""Observation records.

Discovery reasons over *structured observations*, not over prose scraped from
logs or chat. An observation says: while doing this task, in this context, this
class of thing happened, this procedure was used, and this was the outcome.

The two fields discovery actually groups on — ``classification`` and
``procedure_used`` — are normalized deterministically (see
:func:`skillkernel.utils.text.normalize_key`), so "Flaky Import Order" and
"flaky import order" are the same class, and nothing else is.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from skillkernel.core.clock import now_iso
from skillkernel.core.ids import EVIDENCE, OBSERVATION
from skillkernel.core.paths import Layout
from skillkernel.core.schema import (
    Schema,
    enum_spec,
    id_spec,
    int_spec,
    list_spec,
    object_spec,
    str_spec,
    timestamp_spec,
)
from skillkernel.registry import Registry
from skillkernel.utils.text import normalize_key

__all__ = [
    "OBSERVATION_CATEGORIES",
    "OBSERVATION_OUTCOMES",
    "OBSERVATION_SCHEMA",
    "ObservationRecord",
    "ObservationStore",
]

OBSERVATION_SCHEMA_VERSION = 1

OBSERVATION_CATEGORIES = ("failure", "success", "workaround", "pattern")
OBSERVATION_OUTCOMES = ("resolved", "unresolved", "partial")

OBSERVATION_SCHEMA = Schema(
    name="observation",
    supported_versions=(OBSERVATION_SCHEMA_VERSION,),
    root=object_spec(
        {
            "schema_version": int_spec(required=True),
            "id": id_spec(OBSERVATION, required=True),
            "category": enum_spec(OBSERVATION_CATEGORIES, required=True),
            "task": str_spec(required=True, min_length=1),
            "context": str_spec(required=True, min_length=1),
            "classification": str_spec(
                required=True,
                min_length=1,
                description="The failure/pattern class. Discovery groups on this.",
            ),
            "procedure_used": str_spec(
                nullable=True,
                required=True,
                description="The corrective or successful procedure. Discovery groups on this.",
            ),
            "outcome": enum_spec(OBSERVATION_OUTCOMES, required=True),
            "recorded_at": timestamp_spec(required=True),
            "project": str_spec(required=True, min_length=1),
            "evidence": list_spec(id_spec(EVIDENCE), required=True, unique=True),
            "tags": list_spec(str_spec(min_length=1), required=True, unique=True),
            "notes": str_spec(nullable=True),
        },
        unknown="allow_extension",
    ),
)

_HEADER = "# SkillKernel observation record. Discovery reads these; it never scrapes prose.\n"


@dataclass(frozen=True)
class ObservationRecord:
    id: str
    category: str
    task: str
    context: str
    classification: str
    procedure_used: str | None
    outcome: str
    recorded_at: str
    project: str
    evidence: tuple[str, ...]
    tags: tuple[str, ...]
    raw: dict[str, Any]

    @classmethod
    def from_document(cls, document: Any, *, source: str | None = None) -> ObservationRecord:
        data = dict(OBSERVATION_SCHEMA.validate(document, source=source))
        procedure = data["procedure_used"]
        return cls(
            id=str(data["id"]),
            category=str(data["category"]),
            task=str(data["task"]),
            context=str(data["context"]),
            classification=str(data["classification"]),
            procedure_used=None if procedure is None else str(procedure),
            outcome=str(data["outcome"]),
            recorded_at=str(data["recorded_at"]),
            project=str(data["project"]),
            evidence=tuple(str(item) for item in data["evidence"]),
            tags=tuple(str(item) for item in data["tags"]),
            raw=data,
        )

    @property
    def classification_key(self) -> str:
        return normalize_key(self.classification)

    @property
    def procedure_key(self) -> str | None:
        return None if self.procedure_used is None else normalize_key(self.procedure_used)

    def summary_row(self) -> dict[str, str | None]:
        return {
            "category": self.category,
            "classification": self.classification,
            "outcome": self.outcome,
            "recorded_at": self.recorded_at,
        }


class ObservationStore:
    def __init__(self, layout: Layout) -> None:
        self.layout = layout
        self.registry = Registry(
            layout, kind="observations", domain_dir=layout.observations_dir, id_prefix=OBSERVATION
        )

    def ids(self) -> list[str]:
        return self.registry.ids()

    def has(self, record_id: str) -> bool:
        return self.registry.has(record_id)

    def get(self, record_id: str) -> ObservationRecord:
        return ObservationRecord.from_document(self.registry.load(record_id), source=record_id)

    def all(self) -> list[ObservationRecord]:
        return [self.get(record_id) for record_id in self.ids()]

    def add(
        self,
        *,
        category: str,
        task: str,
        context: str,
        classification: str,
        outcome: str,
        project: str,
        procedure_used: str | None = None,
        evidence: Sequence[str] = (),
        tags: Sequence[str] = (),
        notes: str | None = None,
        now: str | None = None,
    ) -> ObservationRecord:
        record_id = self.registry.allocate_id()
        document = {
            "schema_version": OBSERVATION_SCHEMA_VERSION,
            "id": record_id,
            "category": category,
            "task": task,
            "context": context,
            "classification": classification,
            "procedure_used": procedure_used,
            "outcome": outcome,
            "recorded_at": now or now_iso(),
            "project": project,
            "evidence": sorted(set(evidence)),
            "tags": sorted(set(tags)),
            "notes": notes,
        }
        record = ObservationRecord.from_document(document, source=record_id)
        self.registry.put(
            record_id, record.raw, summary=record.summary_row(), expect_new=True, header=_HEADER
        )
        return record
