"""Directory-backed skill storage.

A skill is not a single file. It is a directory — ``skill.yaml`` plus its
promotion history, evaluation definition and example cases — so this store
writes a tree rather than a record, and registers the tree's ``skill.yaml`` in
the skills registry.

Write ordering mirrors the registry's own rule. The history is written first,
then ``skill.yaml`` and the index. An interruption therefore leaves an
unregistered directory, which no reader resolves, rather than a registered skill
with no history, which promotion could not verify.

What this store deliberately does **not** do: change a skill's maturity. That is
the promotion engine's job, because a maturity change without a gate check and a
history entry is precisely the silent state mutation the design forbids.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from skillkernel.core.clock import now_iso
from skillkernel.core.errors import (
    LocationInvariantError,
    RecordNotFoundError,
    ValidationError,
)
from skillkernel.core.ids import SKILL
from skillkernel.core.paths import SKILL_FILENAME, Layout
from skillkernel.registry import Registry
from skillkernel.skills.history import (
    append_transition,
    load_history,
    maturity_path,
    new_history_document,
    write_history,
)
from skillkernel.skills.model import SKILL_SCOPES, SkillRecord, new_skill_document
from skillkernel.utils.text import slugify

__all__ = ["SKILL_FILENAME", "SkillStore", "skills_registry"]

HISTORY_FILENAME = "history.yaml"

_HEADER = (
    "# SkillKernel skill record. Authoritative structured truth for this skill.\n"
    "# Maturity is changed only by the promotion engine, which records every\n"
    "# transition in history.yaml.\n"
)

# Fields a caller may edit directly. Everything else is lifecycle or provenance
# state owned by the kernel: maturity moves through promotion, evidence through
# attach_evidence, identity not at all.
EDITABLE_FIELDS = frozenset(
    {
        "purpose",
        "applies_when",
        "do_not_apply_when",
        "activation_rules",
        "inputs",
        "preconditions",
        "procedure",
        "success_conditions",
        "failure_modes",
        "verification",
        "confidence",
    }
)


def skills_registry(layout: Layout) -> Registry:
    return Registry(layout, kind="skills", domain_dir=layout.skills_dir, id_prefix=SKILL)


class SkillStore:
    def __init__(self, layout: Layout) -> None:
        self.layout = layout
        self.registry = skills_registry(layout)

    # --- locations ---------------------------------------------------------
    def relative_skill_path(self, scope: str, slug: str) -> str:
        """Delegates to the single canonical computation on :class:`Layout`."""
        return self.layout.relative_skill_path(scope, slug)

    def skill_dir(self, skill_id: str) -> Path:
        """The directory holding this skill's files."""
        return self.registry.path_of(skill_id).parent

    def history_path(self, skill_id: str) -> Path:
        return self.skill_dir(skill_id) / HISTORY_FILENAME

    # --- reads -------------------------------------------------------------
    def ids(self) -> list[str]:
        return self.registry.ids()

    def has(self, skill_id: str) -> bool:
        return self.registry.has(skill_id)

    def get(self, skill_id: str) -> SkillRecord:
        return SkillRecord.from_document(self.registry.load(skill_id), source=skill_id)

    def all(self) -> list[SkillRecord]:
        return [self.get(skill_id) for skill_id in self.ids()]

    def in_scope(self, scope: str) -> list[SkillRecord]:
        """Skills belonging to one scope.

        Scope separation is enforced here rather than left to callers: a project
        skill must not surface as a core skill just because it is mature.
        """
        return [record for record in self.all() if record.scope == scope]

    def history(self, skill_id: str) -> dict[str, Any]:
        return load_history(self.history_path(skill_id))

    def maturity_path(self, skill_id: str) -> list[str]:
        return maturity_path(self.history(skill_id))

    def find_by_slug(self, scope: str, slug: str) -> SkillRecord | None:
        for record in self.all():
            if record.scope == scope and record.slug == slug:
                return record
        return None

    # --- writes ------------------------------------------------------------
    def create(
        self,
        *,
        name: str,
        scope: str,
        purpose: str | None = None,
        applies_when: Sequence[str] = (),
        do_not_apply_when: Sequence[str] = (),
        activation_rules: dict[str, list[str]] | None = None,
        created_by: str = "manual",
        created_from: Sequence[str] = (),
        discovery_key: str | None = None,
        origin_project: str | None = None,
        confidence: str = "low",
        slug: str | None = None,
        now: str | None = None,
    ) -> SkillRecord:
        """Create a skill at maturity ``observed`` with an opening history entry."""
        if scope not in SKILL_SCOPES:
            raise ValidationError(f"unknown skill scope {scope!r}; expected one of {SKILL_SCOPES}")
        resolved_slug = slug or slugify(name)
        # Validate the canonical components before anything is written.
        # allocate_id() below persists the registry counter, so checking later
        # would burn an identifier for a slug that was never going to be legal.
        self.layout.relative_skill_path(scope, resolved_slug)
        if self.find_by_slug(scope, resolved_slug) is not None:
            raise ValidationError(
                f"a {scope} skill with slug {resolved_slug!r} already exists; "
                "choose a different name or slug"
            )

        timestamp = now or now_iso()
        skill_id = self.registry.allocate_id()
        document = new_skill_document(
            record_id=skill_id,
            name=name,
            slug=resolved_slug,
            scope=scope,
            now=timestamp,
            created_by=created_by,
            created_from=created_from,
            discovery_key=discovery_key,
            origin_project=origin_project,
            purpose=purpose,
            applies_when=applies_when,
            do_not_apply_when=do_not_apply_when,
            activation_rules=activation_rules,
            confidence=confidence,
        )
        record = SkillRecord.from_document(document, source=skill_id)

        directory = self.layout.require_inside(self.layout.skill_path(scope, resolved_slug).parent)
        directory.mkdir(parents=True, exist_ok=True)

        # History first: an interruption leaves an unregistered directory, which
        # no reader resolves, rather than a registered skill with no history.
        write_history(
            directory / HISTORY_FILENAME,
            new_history_document(skill_id, at=timestamp, actor=created_by, reason="Skill created."),
        )
        self._persist(record, expect_new=True)
        return record

    def _check_location_invariant(self, record: SkillRecord) -> str:
        """Return the canonical path, or raise before anything is written.

        A skill's location is derived from its declared scope and slug. If a
        record being saved would land somewhere other than where it is already
        registered, its identity has moved -- and moving it silently is what
        allowed a second skill to occupy the same directory and overwrite the
        first one's record and history.

        Scope and slug are therefore immutable through ordinary persistence
        (DEC-0011). Relocation is a deliberate, gated, recorded operation that
        does not exist yet; ``save()`` is not it.
        """
        canonical = self.layout.relative_skill_path(record.scope, record.slug)
        entry = self.registry.load_index().entries.get(record.id)
        if entry is None or entry.path == canonical:
            return canonical

        registered = self.layout.parse_relative_skill_path(entry.path)
        if registered is None:
            raise LocationInvariantError(
                f"{record.id} is registered at {entry.path}, which is not a canonical "
                f"skill location. Expected {canonical}."
            )
        registered_scope, registered_slug = registered
        moved = []
        if registered_scope != record.scope:
            moved.append(f"scope {registered_scope!r} -> {record.scope!r}")
        if registered_slug != record.slug:
            moved.append(f"slug {registered_slug!r} -> {record.slug!r}")
        raise LocationInvariantError(
            f"{record.id} cannot change its location-bearing identity through an "
            f"ordinary save ({'; '.join(moved)}). It is stored at {entry.path}; the "
            f"record being saved belongs at {canonical}. A skill's scope and slug are "
            "immutable after creation, because moving one silently would let another "
            "skill occupy its directory and overwrite its record and history. "
            "Relocation requires an explicit gated operation, which does not exist yet."
        )

    def _persist(self, record: SkillRecord, *, expect_new: bool = False) -> SkillRecord:
        """The single write path for a skill record.

        The invariant is checked first, so a rejected save leaves the workspace
        byte-identical: nothing has been written to undo.
        """
        canonical = self._check_location_invariant(record)
        self.registry.put(
            record.id,
            record.raw,
            summary=record.summary_row(),
            relative_path=canonical,
            expect_new=expect_new,
            header=_HEADER,
        )
        return record

    def save(self, record: SkillRecord) -> SkillRecord:
        """Persist a record that already exists, at its canonical location.

        Refuses a record whose declared scope or slug would move it. See
        :meth:`_check_location_invariant`.
        """
        return self._persist(record)

    def update(self, skill_id: str, *, now: str | None = None, **fields: Any) -> SkillRecord:
        """Edit the editable fields of a skill.

        Refuses anything the kernel owns. Maturity in particular: moving it here
        would bypass the gates and leave the promotion history inconsistent.
        """
        if not fields:
            raise ValidationError("update() requires at least one field")
        rejected = sorted(set(fields) - EDITABLE_FIELDS)
        if rejected:
            raise ValidationError(
                f"{', '.join(rejected)} may not be edited directly; "
                f"editable fields are {', '.join(sorted(EDITABLE_FIELDS))}. "
                "Maturity changes go through the promotion engine, and evidence "
                "through attach_evidence()."
            )
        current = self.get(skill_id)
        document = dict(current.raw)
        document.update(fields)
        document["updated_at"] = now or now_iso()
        return self.save(SkillRecord.from_document(document, source=skill_id))

    def attach_evidence(
        self,
        skill_id: str,
        *,
        experiments: Sequence[str] = (),
        knowledge: Sequence[str] = (),
        records: Sequence[str] = (),
        now: str | None = None,
    ) -> SkillRecord:
        """Add evidence references. Existing references are kept, never replaced."""
        current = self.get(skill_id)
        document = dict(current.raw)
        document["evidence"] = {
            "experiments": sorted({*current.experiment_ids, *experiments}),
            "knowledge": sorted({*current.knowledge_ids, *knowledge}),
            "records": sorted({*current.evidence_ids, *records}),
        }
        document["updated_at"] = now or now_iso()
        return self.save(SkillRecord.from_document(document, source=skill_id))

    def record_transition(
        self,
        skill_id: str,
        *,
        previous_state: str,
        new_state: str,
        reason: str,
        actor: str,
        evidence: Sequence[str] = (),
        gate_result: dict[str, Any] | None = None,
        now: str | None = None,
    ) -> SkillRecord:
        """Apply a maturity change and append its history entry.

        Intended for the promotion engine, which checks the transition and the
        gate first. Calling it directly skips those checks.
        """
        current = self.get(skill_id)
        timestamp = now or now_iso()
        document = dict(current.raw)
        document["classification"] = {**current.raw["classification"], "maturity": new_state}
        document["updated_at"] = timestamp
        updated = SkillRecord.from_document(document, source=skill_id)

        # History first, so a maturity is never recorded without its transition.
        append_transition(
            self.history_path(skill_id),
            previous_state=previous_state,
            new_state=new_state,
            at=timestamp,
            reason=reason,
            actor=actor,
            evidence=evidence,
            gate_result=gate_result,
        )
        return self.save(updated)

    def require(self, skill_id: str) -> SkillRecord:
        if not self.has(skill_id):
            raise RecordNotFoundError(f"{skill_id} is not a registered skill")
        return self.get(skill_id)
