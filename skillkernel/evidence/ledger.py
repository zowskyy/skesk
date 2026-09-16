"""The evidence ledger.

Append-oriented and *tamper-evident*. Records are written once and never edited
in place by the kernel, and each carries the identifier and hash of the record
before it, so ``verify()`` detects a record that has been altered, removed,
inserted or reordered after the fact.

The precise guarantee, stated carefully because later promotion decisions rest
on it:

    Unauthorized modification of ledger history becomes *detectable* by
    ``verify()`` under the current trust model.

That is tamper evidence, not immutability. Anyone who can write to the
repository can rewrite a record — and, if they also recompute every subsequent
chain hash, produce a ledger that verifies. The chain raises the cost of a
silent edit from "change one line" to "rewrite the entire remaining history",
and makes the ordinary accident (hand-editing a record, deleting an artifact,
restoring a stale file) loud. Genuine immutability would require an authority
outside the repository, such as signed commits or an append-only remote.

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
from skillkernel.core.errors import (
    IntegrityError,
    SkillKernelError,
    UnsafeOperationError,
    ValidationError,
)
from skillkernel.core.ids import EVIDENCE, parse_id
from skillkernel.core.paths import Layout
from skillkernel.evidence.model import EVIDENCE_SCHEMA_VERSION, EvidenceRecord, chain_hash
from skillkernel.evidence.secrets import scan_bytes, scan_text, scan_truncated
from skillkernel.registry import Registry
from skillkernel.utils.atomic import atomic_write_bytes
from skillkernel.utils.hashing import canonical_json, sha256_bytes, sha256_file

__all__ = [
    "EvidenceLedger",
    "LedgerFinding",
    "resolve_declared_artifact",
]

_HEADER = (
    "# SkillKernel evidence record. Chained to its predecessor: editing this file makes the\n"
    "# ledger fail verification from here onwards. This is tamper evidence, not immutability.\n"
)


ARTIFACT_NAMESPACE = ("evidence", "artifacts")
"""The two path components every declared artifact must begin with."""


def resolve_declared_artifact(layout: Layout, record_id: str, declared: str) -> Path:
    """Resolve a record's declared artifact, or refuse before touching anything.

    A record is a plain file a person can edit, and its ``artifact.path`` used to
    be joined onto the repository root and opened. A hand-edited path reaching
    outside the repository was therefore read and hashed -- and its size reported
    in a finding -- before the hash comparison refused it. The refusal was
    correct; getting there by reading someone else's file was not.

    So the shape is checked **lexically first**, with no filesystem access at
    all: exactly ``evidence/artifacts/<this record>/<one plain name>``. Only a
    path that survives that is resolved, and then
    :meth:`Layout.require_within` proves it against the directory that owns it
    (DEC-0017). Verification and the promotion gate share this one function, so
    they cannot drift into disagreeing about the boundary.
    """
    text = str(declared).replace("\\", "/")
    parts = tuple(part for part in text.split("/") if part != "")
    owner = layout.evidence_artifact_dir(record_id)
    expected = f"{'/'.join(ARTIFACT_NAMESPACE)}/{record_id}/<name>"
    if (
        text.startswith("/")
        or any(part in (".", "..") for part in parts)
        or len(parts) != len(ARTIFACT_NAMESPACE) + 2
        or parts[: len(ARTIFACT_NAMESPACE)] != ARTIFACT_NAMESPACE
        or parts[len(ARTIFACT_NAMESPACE)] != record_id
    ):
        raise UnsafeOperationError(
            f"artifact {declared!r} is not inside {record_id}'s own artifact directory; "
            f"a declared artifact must be {expected}. Nothing was read."
        )
    return layout.require_within(owner, owner / parts[-1])


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

        record_id = self.registry.allocate_id(also_claims=self._artifact_claims)
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

    def _artifact_claims(self, record_id: str) -> list[str]:
        """The artifact directory this identifier is about to claim.

        Claimed whether or not an artifact is supplied: the schema allows zero
        or one, so a record with none owns an *empty* namespace, and a directory
        already sitting there belongs to nobody.
        """
        return [f"artifacts/{record_id}"]

    def _artifact_destination(self, record_id: str, name: str) -> Path:
        safe_name = Path(name).name
        if not safe_name or safe_name in {".", ".."}:
            raise UnsafeOperationError(f"invalid artifact name {name!r}")
        owner = self.layout.evidence_artifact_dir(record_id)
        # Layer A above makes an escape unconstructible from the name; Layer B
        # proves the result against the directory that owns it, so neither is a
        # single point of failure (DEC-0017). The reader asserts the same
        # boundary through ``resolve_declared_artifact``.
        return self.layout.require_within(owner, owner / safe_name)

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
        try:
            path = resolve_declared_artifact(self.layout, record.id, relative)
        except SkillKernelError as exc:
            return [LedgerFinding(record.id, str(exc))]
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
        """The artifact namespace holds exactly what the records declare.

        Closed-world, and deliberately only here. The schema allows zero or one
        artifact per record, so each record's directory may hold exactly its
        declared file and a record declaring none may have no directory at all.
        Anything else -- an extra file, a nested directory, a symlink, a
        directory nobody owns -- is content hiding inside evidence, and evidence
        that can hide things is not evidence.

        This is not a filesystem lint rule. It applies to ``evidence/artifacts/``
        and nowhere else, because that is the one namespace whose entire contents
        the records are supposed to enumerate. Supporting more than one artifact
        per record would need a schema change and a decision to match; VS7 does
        not anticipate one.
        """
        artifacts_dir = self.layout.evidence_artifacts_dir
        if not artifacts_dir.is_dir():
            return []
        declared = self._declared_artifacts()
        findings: list[LedgerFinding] = []
        for child in sorted(artifacts_dir.iterdir()):
            if child.name == ".gitkeep":
                continue
            if child.name not in declared:
                findings.append(
                    LedgerFinding(
                        child.name,
                        f"evidence/artifacts/{child.name} has no matching evidence record",
                    )
                )
                continue
            findings.extend(self._verify_owned_directory(child, declared[child.name]))
        return findings

    def _declared_artifacts(self) -> dict[str, str | None]:
        """Each registered record's declared artifact filename, or None."""
        declared: dict[str, str | None] = {}
        for record_id in self.ids():
            try:
                record = self.get(record_id)
            except SkillKernelError:
                # Already reported by the record checks; do not report twice.
                declared[record_id] = None
                continue
            if record.artifact is None:
                declared[record_id] = None
            else:
                declared[record_id] = Path(str(record.artifact["path"])).name
        return declared

    def _verify_owned_directory(self, directory: Path, expected: str | None) -> list[LedgerFinding]:
        record_id = directory.name
        findings: list[LedgerFinding] = []
        if not directory.is_dir() or directory.is_symlink():
            return [
                LedgerFinding(record_id, f"evidence/artifacts/{record_id} is not a plain directory")
            ]
        for entry in sorted(directory.iterdir()):
            where = f"evidence/artifacts/{record_id}/{entry.name}"
            if entry.is_symlink():
                findings.append(LedgerFinding(record_id, f"{where} is a symlink"))
            elif entry.is_dir():
                findings.append(
                    LedgerFinding(record_id, f"{where} is a directory; artifacts are files")
                )
            elif entry.name != expected:
                findings.append(
                    LedgerFinding(
                        record_id,
                        f"{where} is not referenced by {record_id}; an artifact directory "
                        "holds only the artifact its record declares",
                    )
                )
        if expected is None and not findings and any(directory.iterdir()):
            findings.append(
                LedgerFinding(record_id, f"evidence/artifacts/{record_id} has unexpected contents")
            )
        if expected is None and not any(directory.iterdir()):
            findings.append(
                LedgerFinding(
                    record_id,
                    f"evidence/artifacts/{record_id} exists but {record_id} declares no artifact",
                )
            )
        return findings

    @staticmethod
    def digest_of(payload: bytes) -> str:
        return sha256_bytes(payload)
