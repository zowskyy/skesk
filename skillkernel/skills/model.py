"""The skill contract.

A skill is a procedure plus the conditions under which it applies — and,
equally, the conditions under which it must *not*. Activation boundaries are
first-class here: ``do_not_apply_when`` is required from ``candidate`` onward,
and ``activation_rules`` gives the deterministic, machine-checkable form of
those boundaries that the evaluator scores.

Two hashes matter:

``behavior_fingerprint``
    Covers only what the skill tells a consumer to do and when. Lifecycle
    bookkeeping (maturity, evidence lists, provenance, project scope) is
    excluded on purpose — otherwise promoting a skill would invalidate the very
    evaluation that justified the promotion.

``content_hash``
    Covers the whole document; used to detect edits to a stored record.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from skillkernel.core.ids import EVIDENCE, EXPERIMENT, KNOWLEDGE, SKILL
from skillkernel.core.paths import SKILL_SCOPES
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
from skillkernel.skills.maturity import MATURITIES
from skillkernel.utils.hashing import sha256_mapping

__all__ = [
    "BEHAVIORAL_ROLE",
    "CONFIDENCE_LEVELS",
    "FIELD_ROLES",
    "SKILL_SCHEMA",
    "SKILL_SCOPES",
    "SkillRecord",
    "behavior_fingerprint",
    "new_skill_document",
]

SKILL_SCHEMA_VERSION = 1

CONFIDENCE_LEVELS = ("low", "medium", "high")
CREATED_BY = ("manual", "discovery", "bundled")

_STR_LIST = list_spec(str_spec(min_length=1), required=True)

SKILL_SCHEMA = Schema(
    name="skill",
    supported_versions=(SKILL_SCHEMA_VERSION,),
    root=object_spec(
        {
            "schema_version": int_spec(required=True),
            "id": id_spec(SKILL, required=True),
            "name": str_spec(required=True, min_length=1),
            "slug": str_spec(required=True, min_length=1),
            "version": str_spec(required=True, min_length=1),
            "classification": object_spec(
                {
                    "scope": enum_spec(SKILL_SCOPES, required=True),
                    "maturity": enum_spec(MATURITIES, required=True),
                },
                required=True,
                unknown="allow_extension",
            ),
            "purpose": str_spec(nullable=True, required=True),
            "applies_when": _STR_LIST,
            "do_not_apply_when": _STR_LIST,
            "activation_rules": object_spec(
                {
                    "require_any": list_spec(str_spec(min_length=1), required=True, unique=True),
                    "require_all": list_spec(str_spec(min_length=1), required=True, unique=True),
                    "exclude_any": list_spec(str_spec(min_length=1), required=True, unique=True),
                },
                required=True,
                unknown="allow_extension",
            ),
            "inputs": _STR_LIST,
            "preconditions": _STR_LIST,
            "procedure": _STR_LIST,
            "success_conditions": _STR_LIST,
            "failure_modes": _STR_LIST,
            "verification": _STR_LIST,
            "evidence": object_spec(
                {
                    "experiments": list_spec(id_spec(EXPERIMENT), required=True, unique=True),
                    "knowledge": list_spec(id_spec(KNOWLEDGE), required=True, unique=True),
                    "records": list_spec(id_spec(EVIDENCE), required=True, unique=True),
                },
                required=True,
                unknown="allow_extension",
            ),
            "provenance": object_spec(
                {
                    "created_from": list_spec(str_spec(min_length=1), required=True, unique=True),
                    "created_by": enum_spec(CREATED_BY, required=True),
                    "created_at": timestamp_spec(required=True),
                    "discovery_key": str_spec(nullable=True, required=True),
                },
                required=True,
                unknown="allow_extension",
            ),
            "project_scope": object_spec(
                {
                    "origin_project": str_spec(nullable=True, required=True),
                    "validated_in_projects": list_spec(
                        str_spec(min_length=1), required=True, unique=True
                    ),
                },
                required=True,
                unknown="allow_extension",
            ),
            "confidence": enum_spec(CONFIDENCE_LEVELS, required=True),
            "deprecation": object_spec(
                {
                    "reason": str_spec(required=True, min_length=1),
                    "replaced_by": id_spec(SKILL, nullable=True, required=True),
                    "at": timestamp_spec(required=True),
                },
                nullable=True,
                required=True,
                unknown="allow_extension",
            ),
            "updated_at": timestamp_spec(required=True),
        },
        unknown="allow_extension",
    ),
)

FIELD_ROLES: dict[str, str] = {
    # identity: names the record; changing it makes it a different record.
    "schema_version": "identity",
    "id": "identity",
    "slug": "identity",
    # presentation: how the skill is shown to a human. Renaming a skill does not
    # change what a consumer following it would do.
    "name": "presentation",
    # behavioral: what the skill tells a consumer to do, and when it applies.
    # These, and only these, feed the behavior fingerprint.
    "purpose": "behavioral",
    "applies_when": "behavioral",
    "do_not_apply_when": "behavioral",
    "activation_rules": "behavioral",
    "inputs": "behavioral",
    "preconditions": "behavioral",
    "procedure": "behavioral",
    "success_conditions": "behavioral",
    "failure_modes": "behavioral",
    "verification": "behavioral",
    # lifecycle: where the skill sits in its own maturation. Mutated *by* the
    # kernel as evidence accumulates, so it must not invalidate that evidence.
    "version": "lifecycle",
    "classification": "lifecycle",
    "project_scope": "lifecycle",
    "confidence": "lifecycle",
    "deprecation": "lifecycle",
    "updated_at": "lifecycle",
    # provenance: what the skill rests on. Append-oriented bookkeeping.
    "evidence": "provenance",
    "provenance": "provenance",
}
"""Every top-level skill field, classified by the role it plays.

The classification is the *reason* the behavior fingerprint covers what it
covers, rather than a hand-maintained list of field names. A test asserts that
every field in :data:`SKILL_SCHEMA` appears here, so adding a field to the
schema without deciding its role is a test failure, not a silent omission.
"""

BEHAVIORAL_ROLE = "behavioral"

_BEHAVIOR_FIELDS: tuple[str, ...] = tuple(
    sorted(field for field, role in FIELD_ROLES.items() if role == BEHAVIORAL_ROLE)
)


def behavior_fingerprint(document: dict[str, Any]) -> str:
    """Hash the parts of a skill that determine what it does and when.

    Covers exactly the fields classified ``behavioral`` in :data:`FIELD_ROLES`.

    The exclusions are not incidental. An evaluation is evidence about *this
    procedure with these activation boundaries*; promoting the skill, attaching
    the evidence, or renaming it cannot change whether that evaluation still
    describes the skill. If lifecycle fields were covered, recording a
    promotion would invalidate the evaluation that justified it.
    """
    payload = {key: document.get(key) for key in _BEHAVIOR_FIELDS}
    return sha256_mapping(payload)


@dataclass(frozen=True)
class SkillRecord:
    id: str
    name: str
    slug: str
    version: str
    scope: str
    maturity: str
    purpose: str | None
    applies_when: tuple[str, ...]
    do_not_apply_when: tuple[str, ...]
    activation_rules: dict[str, list[str]]
    inputs: tuple[str, ...]
    preconditions: tuple[str, ...]
    procedure: tuple[str, ...]
    success_conditions: tuple[str, ...]
    failure_modes: tuple[str, ...]
    verification: tuple[str, ...]
    evidence: dict[str, list[str]]
    provenance: dict[str, Any]
    project_scope: dict[str, Any]
    confidence: str
    deprecation: dict[str, Any] | None
    updated_at: str
    raw: dict[str, Any]

    @classmethod
    def from_document(cls, document: Any, *, source: str | None = None) -> SkillRecord:
        data = dict(SKILL_SCHEMA.validate(document, source=source))
        classification = data["classification"]
        rules = data["activation_rules"]
        return cls(
            id=str(data["id"]),
            name=str(data["name"]),
            slug=str(data["slug"]),
            version=str(data["version"]),
            scope=str(classification["scope"]),
            maturity=str(classification["maturity"]),
            purpose=None if data["purpose"] is None else str(data["purpose"]),
            applies_when=tuple(str(item) for item in data["applies_when"]),
            do_not_apply_when=tuple(str(item) for item in data["do_not_apply_when"]),
            activation_rules={key: [str(v) for v in rules[key]] for key in rules},
            inputs=tuple(str(item) for item in data["inputs"]),
            preconditions=tuple(str(item) for item in data["preconditions"]),
            procedure=tuple(str(item) for item in data["procedure"]),
            success_conditions=tuple(str(item) for item in data["success_conditions"]),
            failure_modes=tuple(str(item) for item in data["failure_modes"]),
            verification=tuple(str(item) for item in data["verification"]),
            evidence={key: [str(v) for v in data["evidence"][key]] for key in data["evidence"]},
            provenance=dict(data["provenance"]),
            project_scope=dict(data["project_scope"]),
            confidence=str(data["confidence"]),
            deprecation=None if data["deprecation"] is None else dict(data["deprecation"]),
            updated_at=str(data["updated_at"]),
            raw=data,
        )

    # --- derived -----------------------------------------------------------
    @property
    def experiment_ids(self) -> tuple[str, ...]:
        return tuple(self.evidence.get("experiments", []))

    @property
    def knowledge_ids(self) -> tuple[str, ...]:
        return tuple(self.evidence.get("knowledge", []))

    @property
    def evidence_ids(self) -> tuple[str, ...]:
        return tuple(self.evidence.get("records", []))

    @property
    def discovery_key(self) -> str | None:
        value = self.provenance.get("discovery_key")
        return None if value is None else str(value)

    @property
    def origin_project(self) -> str | None:
        value = self.project_scope.get("origin_project")
        return None if value is None else str(value)

    @property
    def validated_in_projects(self) -> tuple[str, ...]:
        return tuple(str(item) for item in self.project_scope.get("validated_in_projects", []))

    def fingerprint(self) -> str:
        return behavior_fingerprint(self.raw)

    def content_hash(self) -> str:
        return sha256_mapping(self.raw)

    def summary_row(self) -> dict[str, str | None]:
        return {
            "name": self.name,
            "scope": self.scope,
            "maturity": self.maturity,
            "version": self.version,
            "confidence": self.confidence,
        }

    def applies_to(self, signals: Sequence[str]) -> bool:
        """Evaluate the activation rules against a case's signals.

        A skill applies when every ``require_all`` signal is present, at least
        one ``require_any`` signal is present (when that list is non-empty), and
        no ``exclude_any`` signal is present. Exclusions win.
        """
        present = {str(signal).strip().lower() for signal in signals}
        rules = self.activation_rules
        exclude = {value.strip().lower() for value in rules.get("exclude_any", [])}
        if present & exclude:
            return False
        require_all = {value.strip().lower() for value in rules.get("require_all", [])}
        if not require_all.issubset(present):
            return False
        require_any = {value.strip().lower() for value in rules.get("require_any", [])}
        if require_any and not (present & require_any):
            return False
        return bool(require_any or require_all)


def new_skill_document(
    *,
    record_id: str,
    name: str,
    slug: str,
    scope: str,
    now: str,
    created_by: str = "manual",
    created_from: Sequence[str] = (),
    discovery_key: str | None = None,
    origin_project: str | None = None,
    purpose: str | None = None,
    applies_when: Sequence[str] = (),
    do_not_apply_when: Sequence[str] = (),
    activation_rules: dict[str, list[str]] | None = None,
    confidence: str = "low",
    version: str = "0.1.0",
) -> dict[str, Any]:
    """Build a minimal, schema-valid skill document at maturity ``observed``."""
    rules = activation_rules or {}
    return {
        "schema_version": SKILL_SCHEMA_VERSION,
        "id": record_id,
        "name": name,
        "slug": slug,
        "version": version,
        "classification": {"scope": scope, "maturity": "observed"},
        "purpose": purpose,
        "applies_when": list(applies_when),
        "do_not_apply_when": list(do_not_apply_when),
        "activation_rules": {
            "require_any": list(rules.get("require_any", [])),
            "require_all": list(rules.get("require_all", [])),
            "exclude_any": list(rules.get("exclude_any", [])),
        },
        "inputs": [],
        "preconditions": [],
        "procedure": [],
        "success_conditions": [],
        "failure_modes": [],
        "verification": [],
        "evidence": {"experiments": [], "knowledge": [], "records": []},
        "provenance": {
            "created_from": list(created_from),
            "created_by": created_by,
            "created_at": now,
            "discovery_key": discovery_key,
        },
        "project_scope": {
            "origin_project": origin_project,
            "validated_in_projects": [],
        },
        "confidence": confidence,
        "deprecation": None,
        "updated_at": now,
    }
