"""The evidence ledger.

Append-oriented: records are written once and never edited in place. Each record
carries the identifier and hash of the record before it, so ``verify()`` can
prove that no historical record has been altered or removed.

Artifacts are copied into ``evidence/artifacts/<EV-id>/`` and hashed. Recording
an artifact that was not copied (an external report, say) is allowed, but the
record then explicitly carries ``artifact: null`` rather than pretending.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from skillkernel.core.clock import now_iso
from skillkernel.core.errors import IntegrityError, UnsafeOperationError, ValidationError
from skillkernel.core.ids import EVIDENCE, parse_id
from skillkernel.core.paths import Layout
from skillkernel.evidence.model import EVIDENCE_SCHEMA_VERSION, EvidenceRecord, chain_hash
from skillkernel.evidence.secrets import scan_bytes, scan_text, scan_truncated
from skillkernel.registry import Registry
from skillkernel.utils.atomic import atomic_write_bytes
from skillkernel.utils.hashing import canonical_json, sha256_bytes, sha256_file

__all__ = ["EvidenceLedger", "LedgerFinding"]

_HEADER = (
    "# SkillKernel evidence record. Append-only: this file is chained to its predecessor,\n"
    "# so editing it invalidates every later record. Verify with 'skillkernel evidence verify'.\n"
)


@dataclass(frozen=True)
class LedgerFinding:
    """One integrity problem found by :meth:`EvidenceLedger.verify`."""

    evidence_id: str
    message: str

    def __str__(self) -> str:
        return f"{self.evidence_id}: {self.message}"


class EvidenceLedger:
    def __init__(self, layout: Layout) -> None:
        self.layout = layout
        self.registry = Registry(
            layout, kind="evidence", domain_dir=layout.evidence_dir, id_prefix=EVIDENCE
        )

    # --- reads -------------------------------------------------------------
    def ids(self) -> list[str]:
        """Evidence identifiers in ledger (allocation) order."""
        return sorted(self.registry.ids(), key=lambda value: parse_id(value, EVIDENCE).sequence)

    def has(self, record_id: str) -> bool:
        return self.registry.has(record_id)

    def get(self, record_id: str) -> EvidenceRecord:
        return EvidenceRecord.from_document(self.registry.load(record_id), source=record_id)

    def all(self) -> list[EvidenceRecord]:
        return [self.get(record_id) for record_id in self.ids()]

    def head(self) -> EvidenceRecord | None:
        ids = self.ids()
        return self.get(ids[-1]) if ids else None

    def for_skill(self, skill_id: str) -> list[EvidenceRecord]:
        return [record for record in self.all() if record.skill_id == skill_id]

    # --- writes ------------------------------------------------------------
    def record(
        self,
        *,
        kind: str,
        summary: str,
        project: str,
        source_type: str,
        source_detail: str,
        artifact_path: Path | None = None,
        artifact_bytes: bytes | None = None,
        artifact_name: str | None = None,
        media_type: str = "application/octet-stream",
        skill: str | None = None,
        skill_fingerprint: str | None = None,
        experiment: str | None = None,
        experiment_run: str | None = None,
        knowledge: Sequence[str] = (),
        observations: Sequence[str] = (),
        attributes: Mapping[str, Any] | None = None,
        environment: Mapping[str, str | None] | None = None,
        now: str | None = None,
    ) -> EvidenceRecord:
        """Append one evidence record, copying and hashing its artifact.

        Exactly one of ``artifact_path`` or ``artifact_bytes`` may be supplied.
        With neither, the record is stored with ``artifact: null``.
        """
        if artifact_path is not None and artifact_bytes is not None:
            raise ValidationError("supply either artifact_path or artifact_bytes, not both")

        payload: bytes | None = None
        name = artifact_name
        if artifact_path is not None:
            source_file = Path(artifact_path)
            if not source_file.is_file():
                raise ValidationError(f"artifact {source_file} does not exist or is not a file")
            payload = source_file.read_bytes()
            name = name or source_file.name
        elif artifact_bytes is not None:
            payload = artifact_bytes
            if not name:
                raise ValidationError("artifact_name is required when recording raw bytes")

        self._reject_credentials(payload, summary, source_detail, attributes or {})

        record_id = self.registry.allocate_id()
        artifact_block: dict[str, Any] | None = None
        if payload is not None:
            assert name is not None
            destination = self._artifact_destination(record_id, name)
            atomic_write_bytes(destination, payload)
            artifact_block = {
                "path": self.layout.relative(destination),
                "sha256": sha256_file(destination),
                "bytes": len(payload),
                "media_type": media_type,
            }

        previous = self.head()
        previous_id = previous.id if previous else None
        previous_hash = str(previous.chain["hash"]) if previous else None

        document: dict[str, Any] = {
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "id": record_id,
            "kind": kind,
            "summary": summary,
            "project": project,
            "recorded_at": now or now_iso(),
            "source": {"type": source_type, "detail": source_detail},
            "artifact": artifact_block,
            "links": {
                "skill": skill,
                "skill_fingerprint": skill_fingerprint,
                "experiment": experiment,
                "experiment_run": experiment_run,
                "knowledge": sorted(set(knowledge)),
                "observations": sorted(set(observations)),
            },
            "attributes": dict(attributes or {}),
            "environment": dict(environment or {}),
            "chain": {"previous": previous_id, "previous_hash": previous_hash, "hash": ""},
        }
        document["chain"]["hash"] = chain_hash(document, previous_hash)

        record = EvidenceRecord.from_document(document, source=record_id)
        self.registry.put(
            record_id,
            record.raw,
            summary=record.summary_row(),
            expect_new=True,
            header=_HEADER,
        )
        return record

    def _artifact_destination(self, record_id: str, name: str) -> Path:
        safe_name = Path(name).name
        if not safe_name or safe_name in {".", ".."}:
            raise UnsafeOperationError(f"invalid artifact name {name!r}")
        destination = self.layout.evidence_artifacts_dir / record_id / safe_name
        return self.layout.require_inside(destination)

    def _reject_credentials(
        self,
        payload: bytes | None,
        summary: str,
        source_detail: str,
        attributes: Mapping[str, Any],
    ) -> None:
        findings: list[str] = []
        for match in scan_text(f"{summary}\n{source_detail}"):
            findings.append(f"record metadata: {match}")
        for match in scan_text(canonical_json(attributes).decode("utf-8")):
            findings.append(f"record attributes: {match}")
        if payload is not None:
            for match in scan_bytes(payload):
                findings.append(f"artifact content: {match}")
            if scan_truncated(payload):
                findings.append(
                    "artifact content: only the first 4 MiB were scanned for credentials"
                )
        if findings:
            raise UnsafeOperationError(
                "refusing to record evidence that looks like it contains credentials; "
                "redact the content and record it again:\n"
                + "\n".join(f"  - {finding}" for finding in findings)
            )

    # --- integrity ---------------------------------------------------------
    def verify(self) -> list[LedgerFinding]:
        """Re-derive the whole ledger and report every inconsistency.

        Checks, in order: chain linkage, chain hashes, artifact presence and
        artifact content hashes.
        """
        findings: list[LedgerFinding] = []
        previous_id: str | None = None
        previous_hash: str | None = None

        for record_id in self.ids():
            try:
                record = self.get(record_id)
            except (ValidationError, IntegrityError) as exc:
                findings.append(LedgerFinding(record_id, f"record could not be loaded: {exc}"))
                # The chain cannot be continued past an unreadable record.
                return findings

            declared_previous = record.chain.get("previous")
            if declared_previous != previous_id:
                findings.append(
                    LedgerFinding(
                        record_id,
                        f"chain.previous is {declared_previous!r} but the preceding record is "
                        f"{previous_id!r}",
                    )
                )
            declared_previous_hash = record.chain.get("previous_hash")
            if declared_previous_hash != previous_hash:
                findings.append(
                    LedgerFinding(
                        record_id, "chain.previous_hash does not match the preceding record"
                    )
                )

            expected = record.expected_hash(previous_hash)
            actual = str(record.chain.get("hash"))
            if expected != actual:
                findings.append(
                    LedgerFinding(
                        record_id,
                        "chain.hash does not match the record content; the record has been "
                        "modified after it was written",
                    )
                )

            findings.extend(self._verify_artifact(record))
            previous_id = record_id
            previous_hash = actual

        findings.extend(self._verify_no_stray_artifacts())
        return findings

    def _verify_artifact(self, record: EvidenceRecord) -> list[LedgerFinding]:
        if record.artifact is None:
            return []
        relative = str(record.artifact["path"])
        path = self.layout.root / relative
        if not path.is_file():
            return [LedgerFinding(record.id, f"artifact {relative} is missing")]
        try:
            digest = sha256_file(path)
        except OSError as exc:
            return [LedgerFinding(record.id, f"artifact {relative} could not be read: {exc}")]
        findings: list[LedgerFinding] = []
        if digest != record.artifact["sha256"]:
            findings.append(
                LedgerFinding(
                    record.id, f"artifact {relative} content does not match its recorded hash"
                )
            )
        actual_size = path.stat().st_size
        if actual_size != record.artifact["bytes"]:
            findings.append(
                LedgerFinding(
                    record.id,
                    f"artifact {relative} is {actual_size} bytes, recorded as "
                    f"{record.artifact['bytes']}",
                )
            )
        return findings

    def _verify_no_stray_artifacts(self) -> list[LedgerFinding]:
        """Artifact directories that belong to no registered evidence record."""
        artifacts_dir = self.layout.evidence_artifacts_dir
        if not artifacts_dir.is_dir():
            return []
        known = set(self.ids())
        findings: list[LedgerFinding] = []
        for child in sorted(artifacts_dir.iterdir()):
            if child.name == ".gitkeep":
                continue
            if child.name not in known:
                findings.append(
                    LedgerFinding(
                        child.name,
                        f"evidence/artifacts/{child.name} has no matching evidence record",
                    )
                )
        return findings

    @staticmethod
    def digest_of(payload: bytes) -> str:
        return sha256_bytes(payload)
