"""The knowledge record contract.

Knowledge is *truth*, not procedure. A knowledge record says what is believed to
be the case; a skill says what to do about it. Keeping them apart is what lets
the kernel answer "which skills rest on a claim we have since refuted?".

Nothing is ever deleted. A claim that turns out to be wrong becomes ``refuted``
and keeps its identifier, so every skill that cited it stays traceable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from skillkernel.core.ids import EVIDENCE, KNOWLEDGE
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

__all__ = ["KNOWLEDGE_SCHEMA", "KNOWLEDGE_STATUSES", "SOURCE_TYPES", "KnowledgeRecord"]

KNOWLEDGE_SCHEMA_VERSION = 1

KNOWLEDGE_STATUSES = ("proposed", "supported", "refuted", "superseded")
SOURCE_TYPES = ("research", "experiment", "project_observation")

KNOWLEDGE_SCHEMA = Schema(
    name="knowledge",
    supported_versions=(KNOWLEDGE_SCHEMA_VERSION,),
    root=object_spec(
        {
            "schema_version": int_spec(required=True),
            "id": id_spec(KNOWLEDGE, required=True),
            "statement": str_spec(required=True, min_length=1),
            "status": enum_spec(KNOWLEDGE_STATUSES, required=True),
            "scope": str_spec(
                required=True,
                min_length=1,
                description=(
                    "'universal' or 'project:<name>' - where the claim is believed to hold."
                ),
            ),
            "source_type": enum_spec(SOURCE_TYPES, required=True),
            "evidence": list_spec(id_spec(EVIDENCE), required=True, unique=True),
            "experiments": list_spec(str_spec(min_length=1), required=True, unique=True),
            "created_at": timestamp_spec(required=True),
            "updated_at": timestamp_spec(required=True),
            "supersedes": list_spec(id_spec(KNOWLEDGE), required=True, unique=True),
            "superseded_by": id_spec(KNOWLEDGE, nullable=True, required=True),
            "refutation": object_spec(
                {
                    "reason": str_spec(required=True, min_length=1),
                    "evidence": list_spec(id_spec(EVIDENCE), required=True),
                    "at": timestamp_spec(required=True),
                },
                nullable=True,
                required=True,
                unknown="allow_extension",
            ),
            "notes": str_spec(nullable=True),
        },
        unknown="allow_extension",
    ),
)


@dataclass(frozen=True)
class KnowledgeRecord:
    """A validated knowledge record."""

    id: str
    statement: str
    status: str
    scope: str
    source_type: str
    evidence: tuple[str, ...]
    experiments: tuple[str, ...]
    created_at: str
    updated_at: str
    supersedes: tuple[str, ...]
    superseded_by: str | None
    refutation: dict[str, Any] | None
    raw: dict[str, Any]

    @classmethod
    def from_document(cls, document: Any, *, source: str | None = None) -> KnowledgeRecord:
        data = dict(KNOWLEDGE_SCHEMA.validate(document, source=source))
        return cls(
            id=str(data["id"]),
            statement=str(data["statement"]),
            status=str(data["status"]),
            scope=str(data["scope"]),
            source_type=str(data["source_type"]),
            evidence=tuple(str(item) for item in data["evidence"]),
            experiments=tuple(str(item) for item in data["experiments"]),
            created_at=str(data["created_at"]),
            updated_at=str(data["updated_at"]),
            supersedes=tuple(str(item) for item in data["supersedes"]),
            superseded_by=None if data["superseded_by"] is None else str(data["superseded_by"]),
            refutation=None if data["refutation"] is None else dict(data["refutation"]),
            raw=data,
        )

    @property
    def is_usable(self) -> bool:
        """True when a skill may rest on this claim."""
        return self.status in {"proposed", "supported"}

    def summary(self) -> dict[str, str | None]:
        return {
            "status": self.status,
            "scope": self.scope,
            "statement": self.statement
            if len(self.statement) <= 120
            else self.statement[:117] + "...",
        }


def new_knowledge_document(
    *,
    record_id: str,
    statement: str,
    scope: str,
    source_type: str,
    now: str,
    status: str = "proposed",
    evidence: tuple[str, ...] = (),
    experiments: tuple[str, ...] = (),
    notes: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": KNOWLEDGE_SCHEMA_VERSION,
        "id": record_id,
        "statement": statement,
        "status": status,
        "scope": scope,
        "source_type": source_type,
        "evidence": list(evidence),
        "experiments": list(experiments),
        "created_at": now,
        "updated_at": now,
        "supersedes": [],
        "superseded_by": None,
        "refutation": None,
        "notes": notes,
    }
