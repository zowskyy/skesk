"""The portable bundle format, and its frozen content hash.

A bundle carries only definition-level content: what the skill tells a consumer
to do, and the static cases that let a workspace evaluate it. It never carries a
workspace identifier, maturity, confidence, evidence, experiments, evaluation
results, fingerprints, promotion history or timestamps. Those are earned in the
receiving workspace, and a bundle that shipped them would let a skill inherit
evidence it never earned.

The content hash is frozen here, deliberately before the first real bundle
exists, so that the hashing contract is a decision rather than an accident of
whatever the first asset happened to contain.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from skillkernel.core.errors import ValidationError
from skillkernel.core.paths import SKILL_SCOPES, is_canonical_slug, validate_case_id
from skillkernel.core.schema import (
    Schema,
    Spec,
    enum_spec,
    int_spec,
    list_spec,
    number_spec,
    object_spec,
    str_spec,
    validate_value,
)
from skillkernel.utils.hashing import canonical_json, sha256_bytes

__all__ = [
    "BUNDLE_CASE_SCHEMA",
    "BUNDLE_SCHEMA",
    "CONTENT_HASH_PATTERN",
    "HASH_ALGORITHM_ID",
    "MANIFEST_FILENAME",
    "SELF_EXCLUDED_MANIFEST_KEY",
    "SOURCE_FIELD",
    "X_SOURCE_SPEC",
    "Bundle",
    "content_hash",
    "is_content_hash",
    "validate_case",
    "validate_manifest",
    "validate_x_source",
]

BUNDLE_SCHEMA_VERSION = 1

MANIFEST_FILENAME = "bundle.yaml"

HASH_ALGORITHM_ID = b"skillkernel-bundle-content-v1\x00"
"""Domain separator. Changing the framing must change every hash, so the
algorithm identity is part of the hash input."""

SELF_EXCLUDED_MANIFEST_KEY = "content_hash"
"""The one field removed from the manifest before hashing.

The manifest declares the hash of the bundle it belongs to, so including that
field in its own input would be self-referential. This mirrors the exclusion
``definition_hash`` already applies to experiment definitions.
"""

_STR_LIST = list_spec(str_spec(min_length=1), required=True)

BUNDLE_SCHEMA = Schema(
    name="skill-bundle",
    supported_versions=(BUNDLE_SCHEMA_VERSION,),
    root=object_spec(
        {
            "schema_version": int_spec(required=True),
            # --- portable identity ---------------------------------------
            "bundle_id": str_spec(required=True, min_length=1),
            "bundle_version": str_spec(required=True, min_length=1),
            "content_hash": str_spec(nullable=True, required=True),
            # --- the definition the workspace will adopt ------------------
            "name": str_spec(required=True, min_length=1),
            "slug": str_spec(required=True, min_length=1),
            "scope": enum_spec(SKILL_SCOPES, required=True),
            "purpose": str_spec(required=True, min_length=1),
            "applies_when": list_spec(str_spec(min_length=1), required=True, min_items=1),
            "do_not_apply_when": list_spec(str_spec(min_length=1), required=True, min_items=1),
            "activation_rules": object_spec(
                {
                    "require_any": list_spec(str_spec(min_length=1), required=True, unique=True),
                    "require_all": list_spec(str_spec(min_length=1), required=True, unique=True),
                    "exclude_any": list_spec(str_spec(min_length=1), required=True, unique=True),
                },
                required=True,
                unknown="reject",
            ),
            "inputs": _STR_LIST,
            "preconditions": _STR_LIST,
            "procedure": list_spec(str_spec(min_length=1), required=True, min_items=1),
            "success_conditions": _STR_LIST,
            "failure_modes": _STR_LIST,
            "verification": _STR_LIST,
            # --- static evaluation definition -----------------------------
            "evaluation": object_spec(
                {
                    "corpus_id": str_spec(required=True, min_length=1),
                    "pass_threshold": number_spec(required=True, minimum=0.0, maximum=1.0),
                    "max_false_activation_rate": number_spec(
                        required=True, minimum=0.0, maximum=1.0
                    ),
                    "description": str_spec(nullable=True),
                },
                required=True,
                unknown="reject",
            ),
        },
        # Rejected, not extensible: a bundle is external input, and an unknown
        # key is far more likely to be a mistake or an injection attempt than a
        # forward-compatible extension.
        unknown="reject",
    ),
)

BUNDLE_CASE_SCHEMA = Schema(
    name="skill-bundle-case",
    supported_versions=(BUNDLE_SCHEMA_VERSION,),
    root=object_spec(
        {
            "schema_version": int_spec(required=True),
            "case_id": str_spec(required=True, min_length=1),
            "signals": list_spec(str_spec(min_length=1), required=True, min_items=1, unique=True),
            "description": str_spec(nullable=True),
        },
        unknown="reject",
    ),
)
"""A bundled evaluation case.

It carries no ``expected`` field: polarity comes from the directory the case
sits in, so a case cannot disagree with its own folder.
"""

# Fields a bundle must never carry. Checked explicitly so the refusal names the
# offending key rather than emitting a generic "unknown field".
FORBIDDEN_MANIFEST_KEYS = (
    "id",
    "classification",
    "maturity",
    "confidence",
    "evidence",
    "provenance",
    "history",
    "project_scope",
    "deprecation",
    "updated_at",
    "created_at",
    "version",
)


def _frame(path: str, payload: bytes) -> bytes:
    """Length-prefixed framing for one file.

    Fixed-width big-endian lengths make the encoding structurally unambiguous:
    no delimiter can be forged inside a path or a payload, and no two different
    file sets can produce the same byte sequence.
    """
    path_bytes = path.encode("utf-8")
    return (
        len(path_bytes).to_bytes(8, "big") + path_bytes + len(payload).to_bytes(8, "big") + payload
    )


def content_hash(documents: Mapping[str, Any]) -> str:
    """Hash the complete set of portable semantic files.

    ``documents`` maps a normalized POSIX relative path to the *parsed* content
    of that file. Hashing parsed content rather than raw bytes makes the result
    immune to indentation, key ordering and CRLF-versus-LF differences, while
    preserving every semantic distinction: a changed scalar, a reordered list, a
    renamed path, or an added or removed file all change the hash.

    **Precondition.** Every document must already have passed its schema, which
    constrains each field to an exact type. ``canonical_json`` serializes an
    unexpected type via ``str()``, which would let a YAML date collide with the
    equivalent quoted string; schema validation makes that unreachable for a
    valid bundle. The bundle schema therefore rejects such input before this
    function ever sees it.

    The manifest's own ``content_hash`` field is excluded from its input.
    """
    normalized: dict[str, bytes] = {}
    for raw_path, document in documents.items():
        path = _normalize_path(raw_path)
        if path in normalized:
            raise ValidationError(
                f"bundle contains two files that normalize to the same path {path!r}; "
                "ambiguous paths are rejected rather than silently collapsed"
            )
        payload = document
        if path == MANIFEST_FILENAME and isinstance(document, dict):
            payload = {k: v for k, v in document.items() if k != SELF_EXCLUDED_MANIFEST_KEY}
        normalized[path] = canonical_json(payload)

    body = b"".join(_frame(path, normalized[path]) for path in sorted(normalized))
    digest = sha256_bytes(HASH_ALGORITHM_ID + len(normalized).to_bytes(8, "big") + body)
    return f"sha256:{digest}"


def _normalize_path(raw: str) -> str:
    """Normalize to a POSIX relative path, refusing anything ambiguous."""
    path = raw.replace("\\", "/").strip()
    if not path:
        raise ValidationError("bundle contains a file with an empty path")
    if path.startswith("/") or ".." in path.split("/") or "." in path.split("/"):
        raise ValidationError(
            f"bundle file path {raw!r} is not a plain relative path; "
            "'.', '..' and absolute paths are rejected"
        )
    while "//" in path:
        path = path.replace("//", "/")
    return path


@dataclass(frozen=True)
class Bundle:
    """A validated portable definition."""

    bundle_id: str
    bundle_version: str
    content_hash: str
    manifest: dict[str, Any]
    positive_cases: tuple[dict[str, Any], ...]
    negative_cases: tuple[dict[str, Any], ...]

    @property
    def slug(self) -> str:
        return str(self.manifest["slug"])

    @property
    def scope(self) -> str:
        return str(self.manifest["scope"])

    @property
    def name(self) -> str:
        return str(self.manifest["name"])

    @property
    def evaluation(self) -> dict[str, Any]:
        return dict(self.manifest["evaluation"])

    def definition_fields(self) -> dict[str, Any]:
        """Exactly the behavioural fields a workspace record adopts."""
        return {
            key: self.manifest[key]
            for key in (
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
            )
        }

    def source_block(self) -> dict[str, str]:
        """The ``x_source`` provenance this bundle justifies."""
        return {
            "bundle_id": self.bundle_id,
            "bundle_version": self.bundle_version,
            "content_hash": self.content_hash,
        }


def validate_manifest(document: Any, *, source: str) -> dict[str, Any]:
    """Validate a manifest and reject any forbidden lifecycle field."""
    if isinstance(document, dict):
        present = [key for key in FORBIDDEN_MANIFEST_KEYS if key in document]
        if present:
            raise ValidationError(
                f"{source} carries workspace-owned field(s) {', '.join(present)}. "
                "A bundle is a portable definition: identity, maturity, confidence, "
                "evidence, provenance and history belong to the receiving workspace "
                "and must be earned there, never imported."
            )
    data = dict(BUNDLE_SCHEMA.validate(document, source=source))
    if not is_canonical_slug(data["slug"]):
        raise ValidationError(
            f"{source} declares slug {data['slug']!r}, which is not a canonical slug"
        )
    return data


def validate_case(document: Any, *, source: str) -> dict[str, Any]:
    """Validate one bundled case, including its id as a path component.

    The schema types ``case_id`` as a non-empty string, which says nothing about
    the filename it becomes. Checking the grammar here -- the same place
    :func:`validate_manifest` checks the slug -- means a bundle carrying an
    escaping case id cannot even load, so no caller has to remember to guard it.
    """
    data = dict(BUNDLE_CASE_SCHEMA.validate(document, source=source))
    validate_case_id(data["case_id"])
    return data


# --- source provenance ------------------------------------------------------

SOURCE_FIELD = "x_source"
"""Where a skill's bundle origin is recorded, inside ``provenance``.

An ``x_``-prefixed extension rather than a first-class schema field: today
bundles are the only external source, and a record property invented for one
source type would be a guess about every future one. It is promoted to a real
field only when a second source type, or a broader lifecycle requirement,
demonstrates that source provenance is universal (DEC-0014).
"""

CONTENT_HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")

X_SOURCE_SPEC: Spec = object_spec(
    {
        "bundle_id": str_spec(required=True, min_length=1),
        "bundle_version": str_spec(required=True, min_length=1),
        "content_hash": str_spec(required=True, min_length=1),
    },
    required=True,
    # Rejected, not extensible: these three fields answer the three questions
    # source provenance has to answer -- is this the same portable skill, which
    # declared release, and exactly which bytes. A fourth key is a mistake, and
    # silently keeping it would let unaudited state enter a record through the
    # one door the schema leaves open.
    unknown="reject",
)


def is_content_hash(value: object) -> bool:
    return isinstance(value, str) and CONTENT_HASH_PATTERN.match(value) is not None


def validate_x_source(value: Any, *, source: str) -> dict[str, str]:
    """Validate one ``provenance.x_source`` block.

    Used by the installer before writing and by ``doctor`` when reading, so a
    hand-edited record cannot claim a source shape the installer would never
    have produced.
    """
    validate_value(value, X_SOURCE_SPEC, source=f"{source} provenance.{SOURCE_FIELD}")
    assert isinstance(value, dict)
    if not is_content_hash(value["content_hash"]):
        raise ValidationError(
            f"{source} declares {SOURCE_FIELD}.content_hash {value['content_hash']!r}, "
            f"which is not a bundle content hash (expected {CONTENT_HASH_PATTERN.pattern})"
        )
    return {key: str(value[key]) for key in ("bundle_id", "bundle_version", "content_hash")}
