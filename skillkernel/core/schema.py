"""A small declarative schema engine.

Why not ``jsonschema`` or ``pydantic``? The kernel needs three things those
libraries make awkward to control precisely, and little else:

1. an explicit, per-object *unknown field policy* (§4 of the design directive
   requires unknown extension fields to be handled deliberately);
2. error messages carrying a dotted path, for CLI diagnostics;
3. zero runtime dependencies beyond PyYAML.

The engine validates plain Python data loaded from YAML. It never coerces
types: a field declared ``str`` that holds an ``int`` is an error, not a cast,
because silent coercion is how mutable truth drifts.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from skillkernel.core.clock import is_timestamp
from skillkernel.core.errors import ValidationError

__all__ = [
    "Issue",
    "Schema",
    "Spec",
    "any_spec",
    "bool_spec",
    "enum_spec",
    "id_spec",
    "int_spec",
    "list_spec",
    "map_spec",
    "object_spec",
    "str_spec",
    "timestamp_spec",
]

UnknownPolicy = Literal["reject", "allow_extension", "allow"]
"""How an object treats keys that are not in its field list.

``reject``
    Any unknown key is an error.
``allow_extension``
    Keys prefixed ``x_`` are preserved as forward-compatible extensions; every
    other unknown key is an error.
``allow``
    Free-form mapping; used only for opaque payloads such as environment
    fingerprints.
"""

_EXTENSION_PREFIX = "x_"


@dataclass(frozen=True)
class Issue:
    """One schema violation, located by a dotted path."""

    path: str
    message: str
    code: str = "schema"

    def __str__(self) -> str:
        location = self.path or "<root>"
        return f"{location}: {self.message}"


@dataclass(frozen=True)
class Spec:
    """Declarative description of one value.

    ``Spec`` is deliberately a single frozen dataclass rather than a class
    hierarchy: the whole engine fits in one dispatch function, which keeps the
    validation semantics readable in one screenful.
    """

    kind: str
    required: bool = False
    nullable: bool = False
    enum: tuple[str, ...] | None = None
    item: Spec | None = None
    fields: Mapping[str, Spec] = field(default_factory=dict)
    unknown: UnknownPolicy = "reject"
    id_prefix: str | None = None
    min_items: int = 0
    min_length: int = 0
    unique: bool = False
    minimum: float | None = None
    maximum: float | None = None
    description: str = ""


def str_spec(
    *, required: bool = False, nullable: bool = False, min_length: int = 0, description: str = ""
) -> Spec:
    return Spec(
        kind="str",
        required=required,
        nullable=nullable,
        min_length=min_length,
        description=description,
    )


def int_spec(
    *,
    required: bool = False,
    nullable: bool = False,
    minimum: float | None = None,
    maximum: float | None = None,
    description: str = "",
) -> Spec:
    return Spec(
        kind="int",
        required=required,
        nullable=nullable,
        minimum=minimum,
        maximum=maximum,
        description=description,
    )


def number_spec(
    *,
    required: bool = False,
    nullable: bool = False,
    minimum: float | None = None,
    maximum: float | None = None,
    description: str = "",
) -> Spec:
    return Spec(
        kind="number",
        required=required,
        nullable=nullable,
        minimum=minimum,
        maximum=maximum,
        description=description,
    )


def bool_spec(*, required: bool = False, description: str = "") -> Spec:
    return Spec(kind="bool", required=required, description=description)


def enum_spec(
    values: Iterable[str], *, required: bool = False, nullable: bool = False, description: str = ""
) -> Spec:
    return Spec(
        kind="enum",
        required=required,
        nullable=nullable,
        enum=tuple(values),
        description=description,
    )


def id_spec(
    prefix: str, *, required: bool = False, nullable: bool = False, description: str = ""
) -> Spec:
    return Spec(
        kind="id",
        required=required,
        nullable=nullable,
        id_prefix=prefix,
        description=description,
    )


def timestamp_spec(
    *, required: bool = False, nullable: bool = False, description: str = ""
) -> Spec:
    return Spec(kind="timestamp", required=required, nullable=nullable, description=description)


def list_spec(
    item: Spec,
    *,
    required: bool = False,
    min_items: int = 0,
    unique: bool = False,
    description: str = "",
) -> Spec:
    return Spec(
        kind="list",
        required=required,
        item=item,
        min_items=min_items,
        unique=unique,
        description=description,
    )


def object_spec(
    fields: Mapping[str, Spec],
    *,
    required: bool = False,
    nullable: bool = False,
    unknown: UnknownPolicy = "reject",
    description: str = "",
) -> Spec:
    return Spec(
        kind="object",
        required=required,
        nullable=nullable,
        fields=dict(fields),
        unknown=unknown,
        description=description,
    )


def map_spec(
    value: Spec, *, required: bool = False, nullable: bool = False, description: str = ""
) -> Spec:
    return Spec(
        kind="map", required=required, nullable=nullable, item=value, description=description
    )


def any_spec(*, required: bool = False, description: str = "") -> Spec:
    return Spec(kind="any", required=required, nullable=True, description=description)


def _join(path: str, part: str) -> str:
    return f"{path}.{part}" if path else part


def _validate(value: Any, spec: Spec, path: str, issues: list[Issue]) -> None:
    if value is None:
        if not spec.nullable:
            issues.append(Issue(path, "must not be null"))
        return

    kind = spec.kind
    if kind == "any":
        return

    if kind == "str":
        if not isinstance(value, str):
            issues.append(Issue(path, f"expected a string, got {type(value).__name__}"))
        elif len(value.strip()) < spec.min_length:
            issues.append(Issue(path, f"must contain at least {spec.min_length} character(s)"))
        return

    if kind == "int":
        # bool is a subclass of int; an accidental `true` must not pass as 1.
        if isinstance(value, bool) or not isinstance(value, int):
            issues.append(Issue(path, f"expected an integer, got {type(value).__name__}"))
            return
        _check_range(value, spec, path, issues)
        return

    if kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            issues.append(Issue(path, f"expected a number, got {type(value).__name__}"))
            return
        _check_range(float(value), spec, path, issues)
        return

    if kind == "bool":
        if not isinstance(value, bool):
            issues.append(Issue(path, f"expected a boolean, got {type(value).__name__}"))
        return

    if kind == "enum":
        allowed = spec.enum or ()
        if not isinstance(value, str) or value not in allowed:
            issues.append(Issue(path, f"must be one of {', '.join(allowed)}; got {value!r}"))
        return

    if kind == "timestamp":
        if not isinstance(value, str) or not is_timestamp(value):
            issues.append(
                Issue(path, f"must be a UTC timestamp like 2024-01-31T12:00:00Z; got {value!r}")
            )
        return

    if kind == "id":
        _validate_id(value, spec, path, issues)
        return

    if kind == "list":
        _validate_list(value, spec, path, issues)
        return

    if kind == "map":
        _validate_map(value, spec, path, issues)
        return

    if kind == "object":
        _validate_object(value, spec, path, issues)
        return

    raise AssertionError(f"unknown spec kind {kind!r}")


def _check_range(value: float, spec: Spec, path: str, issues: list[Issue]) -> None:
    if spec.minimum is not None and value < spec.minimum:
        issues.append(Issue(path, f"must be >= {spec.minimum}; got {value}"))
    if spec.maximum is not None and value > spec.maximum:
        issues.append(Issue(path, f"must be <= {spec.maximum}; got {value}"))


def _validate_id(value: Any, spec: Spec, path: str, issues: list[Issue]) -> None:
    from skillkernel.core.ids import is_valid_id  # imported here to avoid a cycle

    prefix = spec.id_prefix or ""
    if not isinstance(value, str) or not is_valid_id(value, prefix):
        issues.append(Issue(path, f"must be an identifier like {prefix}-0001; got {value!r}"))


def _validate_list(value: Any, spec: Spec, path: str, issues: list[Issue]) -> None:
    if not isinstance(value, list):
        issues.append(Issue(path, f"expected a list, got {type(value).__name__}"))
        return
    if len(value) < spec.min_items:
        issues.append(
            Issue(path, f"must contain at least {spec.min_items} item(s); got {len(value)}")
        )
    if spec.unique:
        seen: list[Any] = []
        for entry in value:
            hashable = entry if isinstance(entry, (str, int, float, bool)) else repr(entry)
            if hashable in seen:
                issues.append(Issue(path, f"contains duplicate entry {entry!r}"))
                break
            seen.append(hashable)
    item_spec = spec.item
    if item_spec is None:
        return
    for index, entry in enumerate(value):
        _validate(entry, item_spec, f"{path}[{index}]", issues)


def _validate_map(value: Any, spec: Spec, path: str, issues: list[Issue]) -> None:
    if not isinstance(value, dict):
        issues.append(Issue(path, f"expected a mapping, got {type(value).__name__}"))
        return
    item_spec = spec.item
    for key, entry in value.items():
        if not isinstance(key, str):
            issues.append(Issue(path, f"mapping keys must be strings; got {key!r}"))
            continue
        if item_spec is not None:
            _validate(entry, item_spec, _join(path, key), issues)


def _validate_object(value: Any, spec: Spec, path: str, issues: list[Issue]) -> None:
    if not isinstance(value, dict):
        issues.append(Issue(path, f"expected a mapping, got {type(value).__name__}"))
        return
    for key in value:
        if not isinstance(key, str):
            issues.append(Issue(path, f"mapping keys must be strings; got {key!r}"))
    for name, field_spec in spec.fields.items():
        if name in value:
            _validate(value[name], field_spec, _join(path, name), issues)
        elif field_spec.required:
            issues.append(Issue(_join(path, name), "is required but missing", code="missing-field"))
    if spec.unknown == "allow":
        return
    for key in value:
        if not isinstance(key, str) or key in spec.fields:
            continue
        if spec.unknown == "allow_extension" and key.startswith(_EXTENSION_PREFIX):
            continue
        hint = (
            f"unknown field; forward-compatible extensions must be prefixed '{_EXTENSION_PREFIX}'"
            if spec.unknown == "allow_extension"
            else "unknown field"
        )
        issues.append(Issue(_join(path, key), hint, code="unknown-field"))


@dataclass(frozen=True)
class Schema:
    """A named, versioned record schema."""

    name: str
    supported_versions: tuple[int, ...]
    root: Spec

    def issues(self, data: Any) -> list[Issue]:
        """Return every violation in ``data`` (empty list means valid)."""
        issues: list[Issue] = []
        if not isinstance(data, dict):
            return [
                Issue("", f"expected a mapping at the document root, got {type(data).__name__}")
            ]
        version = data.get("schema_version")
        if version is None:
            issues.append(Issue("schema_version", "is required but missing", code="missing-field"))
        elif isinstance(version, bool) or not isinstance(version, int):
            issues.append(
                Issue("schema_version", f"expected an integer, got {type(version).__name__}")
            )
        elif version not in self.supported_versions:
            supported = ", ".join(str(v) for v in self.supported_versions)
            issues.append(
                Issue(
                    "schema_version",
                    f"version {version} is not supported by {self.name} (supported: {supported})",
                    code="unsupported-version",
                )
            )
        _validate_object(data, self.root, "", issues)
        return issues

    def is_valid(self, data: Any) -> bool:
        return not self.issues(data)

    def validate(self, data: Any, *, source: str | None = None) -> Mapping[str, Any]:
        """Return ``data`` unchanged, or raise :class:`ValidationError`."""
        issues = self.issues(data)
        if issues:
            where = f" in {source}" if source else ""
            raise ValidationError(
                f"{self.name} record is invalid{where}", [str(issue) for issue in issues]
            )
        assert isinstance(data, dict)
        return data


def format_issues(issues: Sequence[Issue]) -> list[str]:
    return [str(issue) for issue in issues]
