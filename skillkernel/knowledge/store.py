"""Knowledge storage and lifecycle operations.

The store is where the lineage invariants live:

* a ``refuted`` record must carry a reason (and keeps its identifier forever);
* ``superseded_by``/``supersedes`` are maintained as a *pair*, so lineage can be
  walked in both directions and ``doctor`` can prove no half-link exists;
* a record is never removed, only re-statused.
"""

from __future__ import annotations

from skillkernel.core.clock import now_iso
from skillkernel.core.errors import IntegrityError, ValidationError
from skillkernel.core.ids import KNOWLEDGE
from skillkernel.core.paths import Layout
from skillkernel.knowledge.model import KnowledgeRecord, new_knowledge_document
from skillkernel.registry import Registry

__all__ = ["KnowledgeStore"]

_HEADER = (
    "# SkillKernel knowledge record. Refuted and superseded records are kept, never deleted.\n"
)


class KnowledgeStore:
    def __init__(self, layout: Layout) -> None:
        self.layout = layout
        self.registry = Registry(
            layout, kind="knowledge", domain_dir=layout.knowledge_dir, id_prefix=KNOWLEDGE
        )

    # --- reads -------------------------------------------------------------
    def ids(self) -> list[str]:
        return self.registry.ids()

    def get(self, record_id: str) -> KnowledgeRecord:
        document = self.registry.load(record_id)
        return KnowledgeRecord.from_document(document, source=record_id)

    def has(self, record_id: str) -> bool:
        return self.registry.has(record_id)

    def all(self) -> list[KnowledgeRecord]:
        return [self.get(record_id) for record_id in self.ids()]

    # --- writes ------------------------------------------------------------
    def _save(self, record: KnowledgeRecord, *, expect_new: bool = False) -> KnowledgeRecord:
        self.registry.put(
            record.id,
            record.raw,
            summary=record.summary(),
            expect_new=expect_new,
            header=_HEADER,
        )
        return record

    def add(
        self,
        *,
        statement: str,
        scope: str,
        source_type: str,
        status: str = "proposed",
        evidence: tuple[str, ...] = (),
        experiments: tuple[str, ...] = (),
        notes: str | None = None,
        now: str | None = None,
    ) -> KnowledgeRecord:
        record_id = self.registry.allocate_id()
        document = new_knowledge_document(
            record_id=record_id,
            statement=statement,
            scope=scope,
            source_type=source_type,
            now=now or now_iso(),
            status=status,
            evidence=evidence,
            experiments=experiments,
            notes=notes,
        )
        record = KnowledgeRecord.from_document(document, source=record_id)
        return self._save(record, expect_new=True)

    def set_status(self, record_id: str, status: str, *, now: str | None = None) -> KnowledgeRecord:
        """Move a record between ``proposed`` and ``supported``.

        ``refuted`` and ``superseded`` have dedicated operations because they
        carry mandatory lineage; allowing them here would let a caller reach a
        state the invariants forbid.
        """
        if status not in {"proposed", "supported"}:
            raise ValidationError(
                f"use refute() or supersede() to reach status {status!r}; set_status handles "
                "only 'proposed' and 'supported'"
            )
        current = self.get(record_id)
        if current.status in {"refuted", "superseded"}:
            raise ValidationError(
                f"{record_id} is {current.status} and cannot be returned to {status!r}; "
                "record a new knowledge statement instead"
            )
        document = dict(current.raw)
        document["status"] = status
        document["updated_at"] = now or now_iso()
        return self._save(KnowledgeRecord.from_document(document, source=record_id))

    def refute(
        self,
        record_id: str,
        *,
        reason: str,
        evidence: tuple[str, ...] = (),
        now: str | None = None,
    ) -> KnowledgeRecord:
        """Mark a claim refuted. The record survives; only its status changes."""
        if not reason.strip():
            raise ValidationError("a refutation must state a reason")
        current = self.get(record_id)
        timestamp = now or now_iso()
        document = dict(current.raw)
        document["status"] = "refuted"
        document["updated_at"] = timestamp
        document["refutation"] = {"reason": reason, "evidence": list(evidence), "at": timestamp}
        return self._save(KnowledgeRecord.from_document(document, source=record_id))

    def supersede(
        self, old_id: str, new_id: str, *, now: str | None = None
    ) -> tuple[KnowledgeRecord, KnowledgeRecord]:
        """Replace ``old_id`` with ``new_id``, writing both halves of the link."""
        if old_id == new_id:
            raise ValidationError("a knowledge record cannot supersede itself")
        old = self.get(old_id)
        new = self.get(new_id)
        if old.status == "superseded" and old.superseded_by != new_id:
            raise IntegrityError(
                f"{old_id} is already superseded by {old.superseded_by}; supersede it again only "
                "after correcting that link"
            )
        timestamp = now or now_iso()

        new_document = dict(new.raw)
        supersedes = list(new.supersedes)
        if old_id not in supersedes:
            supersedes.append(old_id)
        new_document["supersedes"] = sorted(supersedes)
        new_document["updated_at"] = timestamp
        updated_new = KnowledgeRecord.from_document(new_document, source=new_id)

        old_document = dict(old.raw)
        old_document["status"] = "superseded"
        old_document["superseded_by"] = new_id
        old_document["updated_at"] = timestamp
        updated_old = KnowledgeRecord.from_document(old_document, source=old_id)

        # Write the successor first: if this is interrupted, the predecessor is
        # still 'supported' and doctor sees a one-sided link, which is a warning.
        # The reverse order would leave a record pointing at a successor that
        # does not yet claim it.
        self._save(updated_new)
        self._save(updated_old)
        return updated_old, updated_new

    def attach_evidence(
        self, record_id: str, evidence_ids: tuple[str, ...], *, now: str | None = None
    ) -> KnowledgeRecord:
        current = self.get(record_id)
        document = dict(current.raw)
        merged = sorted({*current.evidence, *evidence_ids})
        document["evidence"] = merged
        document["updated_at"] = now or now_iso()
        return self._save(KnowledgeRecord.from_document(document, source=record_id))

    def refuted_ids(self) -> set[str]:
        return {record.id for record in self.all() if record.status == "refuted"}

    def lineage_issues(self) -> list[str]:
        """Return every broken lineage link (used by ``doctor``)."""
        issues: list[str] = []
        records = {record.id: record for record in self.all()}
        for record in records.values():
            if record.status == "refuted" and not record.refutation:
                issues.append(f"{record.id} is refuted but carries no refutation reason")
            if record.status == "superseded" and record.superseded_by is None:
                issues.append(f"{record.id} is superseded but names no successor")
            if record.superseded_by is not None:
                successor = records.get(record.superseded_by)
                if successor is None:
                    issues.append(
                        f"{record.id} is superseded by {record.superseded_by}, which does not exist"
                    )
                elif record.id not in successor.supersedes:
                    issues.append(
                        f"{record.id} names {record.superseded_by} as its successor, but "
                        f"{record.superseded_by}.supersedes does not list {record.id}"
                    )
                if record.status != "superseded":
                    issues.append(
                        f"{record.id} names a successor but its status is {record.status!r}, "
                        "expected 'superseded'"
                    )
            for predecessor_id in record.supersedes:
                predecessor = records.get(predecessor_id)
                if predecessor is None:
                    issues.append(
                        f"{record.id} claims to supersede {predecessor_id}, which does not exist"
                    )
                elif predecessor.superseded_by != record.id:
                    issues.append(
                        f"{record.id} claims to supersede {predecessor_id}, but that record points "
                        f"at {predecessor.superseded_by}"
                    )
        return issues
