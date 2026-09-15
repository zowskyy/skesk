"""The evidence record contract.

An evidence record is a claim that *this artifact existed, with this content, at
this time, produced by this thing*. It is the join point of the whole system:

    decision -> skill -> experiment -> evidence -> artifact

and, read the other way, an artifact can be traced back to the conclusions that
rest on it.

Records are chained: each carries the identifier and hash of its predecessor, so
editing or removing an historical record invalidates every record after it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from skillkernel.core.ids import EVIDENCE, EXPERIMENT, KNOWLEDGE, OBSERVATION, SKILL
from skillkernel.core.schema import (
    Schema,
    any_spec,
    enum_spec,
    id_spec,
    int_spec,
    list_spec,
    map_spec,
    object_spec,
    str_spec,
    timestamp_spec,
)
from skillkernel.utils.hashing import sha256_mapping

__all__ = ["EVIDENCE_KINDS", "EVIDENCE_SCHEMA", "EvidenceRecord", "chain_hash"]

EVIDENCE_SCHEMA_VERSION = 1

EVIDENCE_KINDS = (
    "file",
    "command_output",
    "evaluation_report",
    "experiment_run",
    "observation_note",
    "external_report",
)

EVIDENCE_SCHEMA = Schema(
    name="evidence",
    supported_versions=(EVIDENCE_SCHEMA_VERSION,),
    root=object_spec(
        {
            "schema_version": int_spec(required=True),
            "id": id_spec(EVIDENCE, required=True),
            "kind": enum_spec(EVIDENCE_KINDS, required=True),
            "summary": str_spec(required=True, min_length=1),
            "project": str_spec(
                required=True,
                min_length=1,
                description="Project that produced this evidence; drives cross-project promotion.",
            ),
            "recorded_at": timestamp_spec(required=True),
            "source": object_spec(
                {
                    "type": str_spec(required=True, min_length=1),
                    "detail": str_spec(required=True, min_length=1),
                },
                required=True,
                unknown="allow_extension",
            ),
            "artifact": object_spec(
                {
                    "path": str_spec(required=True, min_length=1),
                    "sha256": str_spec(required=True, min_length=64),
                    "bytes": int_spec(required=True, minimum=0),
                    "media_type": str_spec(required=True, min_length=1),
                },
                required=True,
                nullable=True,
                unknown="allow_extension",
            ),
            "links": object_spec(
                {
                    "skill": id_spec(SKILL, nullable=True, required=True),
                    "skill_fingerprint": str_spec(
                        nullable=True,
                        required=True,
                        description="Hash of skill.yaml when this evidence was produced.",
                    ),
                    "experiment": id_spec(EXPERIMENT, nullable=True, required=True),
                    "experiment_run": str_spec(nullable=True, required=True),
                    "knowledge": list_spec(id_spec(KNOWLEDGE), required=True, unique=True),
                    "observations": list_spec(id_spec(OBSERVATION), required=True, unique=True),
                },
                required=True,
                unknown="allow_extension",
            ),
            "attributes": map_spec(any_spec(), required=True),
            "environment": map_spec(str_spec(nullable=True), required=True),
            "chain": object_spec(
                {
                    "previous": id_spec(EVIDENCE, nullable=True, required=True),
                    "previous_hash": str_spec(nullable=True, required=True),
                    "hash": str_spec(required=True, min_length=64),
                },
                required=True,
                unknown="reject",
            ),
        },
        unknown="allow_extension",
    ),
)


def chain_hash(document: dict[str, Any], previous_hash: str | None) -> str:
    """Compute the chain hash of ``document``.

    The record's own ``chain.hash`` is excluded from the input (it is the
    output), but ``chain.previous`` and ``chain.previous_hash`` are included, so
    re-pointing a record at a different predecessor changes its hash.
    """
    payload = dict(document)
    chain = dict(payload.get("chain") or {})
    chain.pop("hash", None)
    payload["chain"] = chain
    return sha256_mapping({"previous_hash": previous_hash, "record": payload})


@dataclass(frozen=True)
class EvidenceRecord:
    id: str
    kind: str
    summary: str
    project: str
    recorded_at: str
    source: dict[str, Any]
    artifact: dict[str, Any] | None
    links: dict[str, Any]
    attributes: dict[str, Any]
    environment: dict[str, str | None]
    chain: dict[str, Any]
    raw: dict[str, Any]

    @classmethod
    def from_document(cls, document: Any, *, source: str | None = None) -> EvidenceRecord:
        data = dict(EVIDENCE_SCHEMA.validate(document, source=source))
        return cls(
            id=str(data["id"]),
            kind=str(data["kind"]),
            summary=str(data["summary"]),
            project=str(data["project"]),
            recorded_at=str(data["recorded_at"]),
            source=dict(data["source"]),
            artifact=None if data["artifact"] is None else dict(data["artifact"]),
            links=dict(data["links"]),
            attributes=dict(data["attributes"]),
            environment={str(k): v for k, v in data["environment"].items()},
            chain=dict(data["chain"]),
            raw=data,
        )

    @property
    def skill_id(self) -> str | None:
        value = self.links.get("skill")
        return None if value is None else str(value)

    @property
    def skill_fingerprint(self) -> str | None:
        value = self.links.get("skill_fingerprint")
        return None if value is None else str(value)

    @property
    def experiment_id(self) -> str | None:
        value = self.links.get("experiment")
        return None if value is None else str(value)

    @property
    def knowledge_ids(self) -> tuple[str, ...]:
        return tuple(str(item) for item in self.links.get("knowledge", []))

    @property
    def observation_ids(self) -> tuple[str, ...]:
        return tuple(str(item) for item in self.links.get("observations", []))

    def expected_hash(self, previous_hash: str | None) -> str:
        return chain_hash(self.raw, previous_hash)

    def summary_row(self) -> dict[str, str | None]:
        return {
            "kind": self.kind,
            "project": self.project,
            "recorded_at": self.recorded_at,
            "skill": self.skill_id,
            "experiment": self.experiment_id,
        }
