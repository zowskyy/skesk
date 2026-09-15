"""Experiment definitions and results.

Two rules make an experiment worth trusting, and both are mechanical here:

**Freeze before you look.** A definition must be frozen — thresholds, metrics,
corpus and scorer fixed, and the whole thing hashed — before any result may be
recorded against it. Changing a frozen definition changes its hash, and
``verify`` reports it.

**The kernel computes the verdict.** The caller supplies measurements. Whether
those measurements pass is derived from the frozen thresholds by
:func:`evaluate_verdict`, so a gate cannot be moved after the numbers are in.
Moving a gate requires ``revise()``, which writes a *new version* and leaves the
original file untouched.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from skillkernel.core.errors import ValidationError
from skillkernel.core.ids import EXPERIMENT, KNOWLEDGE
from skillkernel.core.schema import (
    Schema,
    bool_spec,
    enum_spec,
    id_spec,
    int_spec,
    list_spec,
    map_spec,
    number_spec,
    object_spec,
    str_spec,
    timestamp_spec,
)
from skillkernel.utils.hashing import sha256_mapping

__all__ = [
    "EXPERIMENT_SCHEMA",
    "RESULT_SCHEMA",
    "VERDICTS",
    "ExperimentDefinition",
    "ExperimentResult",
    "definition_hash",
    "evaluate_verdict",
    "new_experiment_document",
]

EXPERIMENT_SCHEMA_VERSION = 1
RESULT_SCHEMA_VERSION = 1

DIRECTIONS = ("maximize", "minimize")
VERDICTS = ("pass", "fail", "inconclusive")

_METRIC_SPEC = object_spec(
    {
        "name": str_spec(required=True, min_length=1),
        "direction": enum_spec(DIRECTIONS, required=True),
        "description": str_spec(nullable=True),
    },
    required=True,
    unknown="allow_extension",
)

_GUARDRAIL_SPEC = object_spec(
    {
        "name": str_spec(required=True, min_length=1),
        "direction": enum_spec(DIRECTIONS, required=True),
        "threshold": number_spec(required=True),
        "description": str_spec(nullable=True),
    },
    unknown="allow_extension",
)

_ARM_SPEC = object_spec(
    {
        "label": str_spec(required=True, min_length=1),
        "description": str_spec(required=True, min_length=1),
    },
    required=True,
    unknown="allow_extension",
)

EXPERIMENT_SCHEMA = Schema(
    name="experiment",
    supported_versions=(EXPERIMENT_SCHEMA_VERSION,),
    root=object_spec(
        {
            "schema_version": int_spec(required=True),
            "id": id_spec(EXPERIMENT, required=True),
            "version": int_spec(required=True, minimum=1),
            "title": str_spec(required=True, min_length=1),
            "hypothesis": str_spec(required=True, min_length=1),
            "independent_variable": str_spec(required=True, min_length=1),
            "control": _ARM_SPEC,
            "treatment": _ARM_SPEC,
            "corpus": object_spec(
                {
                    "id": str_spec(required=True, min_length=1),
                    "description": str_spec(required=True, min_length=1),
                    "cases": list_spec(
                        str_spec(min_length=1), required=True, min_items=1, unique=True
                    ),
                },
                required=True,
                unknown="allow_extension",
            ),
            "scorer": object_spec(
                {
                    "name": str_spec(required=True, min_length=1),
                    "version": str_spec(required=True, min_length=1),
                    "deterministic": bool_spec(required=True),
                },
                required=True,
                unknown="allow_extension",
            ),
            "primary_metric": _METRIC_SPEC,
            "guardrail_metrics": list_spec(_GUARDRAIL_SPEC, required=True),
            "pass_threshold": number_spec(required=True),
            "failure_threshold": number_spec(required=True),
            "environment_fingerprint": map_spec(str_spec(nullable=True), required=True),
            "tool_versions": map_spec(str_spec(nullable=True), required=True),
            "implementation_fingerprint": str_spec(nullable=True, required=True),
            "knowledge": list_spec(id_spec(KNOWLEDGE), required=True, unique=True),
            "project": str_spec(required=True, min_length=1),
            "created_at": timestamp_spec(required=True),
            "updated_at": timestamp_spec(required=True),
            "frozen": bool_spec(required=True),
            "frozen_at": timestamp_spec(nullable=True, required=True),
            "definition_hash": str_spec(nullable=True, required=True),
            "supersedes_version": int_spec(nullable=True, required=True),
            "revision_reason": str_spec(nullable=True, required=True),
        },
        unknown="allow_extension",
    ),
)

RESULT_SCHEMA = Schema(
    name="experiment-result",
    supported_versions=(RESULT_SCHEMA_VERSION,),
    root=object_spec(
        {
            "schema_version": int_spec(required=True),
            "run_id": str_spec(required=True, min_length=1),
            "experiment": id_spec(EXPERIMENT, required=True),
            "experiment_version": int_spec(required=True, minimum=1),
            "definition_hash": str_spec(required=True, min_length=64),
            "started_at": timestamp_spec(required=True),
            "finished_at": timestamp_spec(required=True),
            "control_metrics": map_spec(number_spec(), required=True),
            "treatment_metrics": map_spec(number_spec(), required=True),
            "primary_metric_value": number_spec(required=True),
            "guardrail_values": map_spec(number_spec(), required=True),
            "guardrail_violations": list_spec(str_spec(min_length=1), required=True),
            "verdict": enum_spec(VERDICTS, required=True),
            "interpretation": str_spec(required=True, min_length=1),
            "evidence": list_spec(str_spec(min_length=1), required=True, unique=True),
            "environment": map_spec(str_spec(nullable=True), required=True),
        },
        unknown="allow_extension",
    ),
)

_HASH_EXCLUDED = frozenset({"definition_hash", "updated_at"})


def definition_hash(document: dict[str, Any]) -> str:
    """Hash the scientific content of a definition.

    ``definition_hash`` itself and ``updated_at`` are excluded: the first is the
    output, the second is bookkeeping that must not make an unchanged definition
    look tampered with. Everything else — including ``frozen`` and the
    thresholds — is covered.
    """
    payload = {key: value for key, value in document.items() if key not in _HASH_EXCLUDED}
    return sha256_mapping(payload)


def check_threshold_ordering(
    direction: str, pass_threshold: float, failure_threshold: float
) -> None:
    """Reject gate pairs that can never be satisfied coherently."""
    if direction == "maximize" and failure_threshold > pass_threshold:
        raise ValidationError(
            f"for a maximized metric, failure_threshold ({failure_threshold}) must be <= "
            f"pass_threshold ({pass_threshold})"
        )
    if direction == "minimize" and failure_threshold < pass_threshold:
        raise ValidationError(
            f"for a minimized metric, failure_threshold ({failure_threshold}) must be >= "
            f"pass_threshold ({pass_threshold})"
        )


def evaluate_verdict(
    *,
    direction: str,
    value: float,
    pass_threshold: float,
    failure_threshold: float,
    guardrail_violations: list[str],
) -> str:
    """Derive a verdict from measurements and frozen thresholds.

    A guardrail violation forces ``fail`` regardless of the primary metric: a
    treatment that wins on the headline number while breaking a guardrail has
    not been shown to work.
    """
    if guardrail_violations:
        return "fail"
    if direction == "maximize":
        if value >= pass_threshold:
            return "pass"
        if value <= failure_threshold:
            return "fail"
        return "inconclusive"
    if value <= pass_threshold:
        return "pass"
    if value >= failure_threshold:
        return "fail"
    return "inconclusive"


def find_guardrail_violations(
    guardrails: list[dict[str, Any]], values: dict[str, float]
) -> list[str]:
    """Return the names of guardrails whose measured value breaches its threshold."""
    violations: list[str] = []
    for guardrail in guardrails:
        name = str(guardrail["name"])
        if name not in values:
            violations.append(f"{name} (not measured)")
            continue
        measured = float(values[name])
        threshold = float(guardrail["threshold"])
        direction = str(guardrail["direction"])
        breached = measured < threshold if direction == "maximize" else measured > threshold
        if breached:
            violations.append(name)
    return violations


@dataclass(frozen=True)
class ExperimentDefinition:
    id: str
    version: int
    title: str
    hypothesis: str
    project: str
    frozen: bool
    frozen_at: str | None
    stored_hash: str | None
    primary_metric: dict[str, Any]
    guardrail_metrics: list[dict[str, Any]]
    pass_threshold: float
    failure_threshold: float
    corpus: dict[str, Any]
    knowledge: tuple[str, ...]
    raw: dict[str, Any]

    @classmethod
    def from_document(cls, document: Any, *, source: str | None = None) -> ExperimentDefinition:
        data = dict(EXPERIMENT_SCHEMA.validate(document, source=source))
        primary = dict(data["primary_metric"])
        check_threshold_ordering(
            str(primary["direction"]),
            float(data["pass_threshold"]),
            float(data["failure_threshold"]),
        )
        return cls(
            id=str(data["id"]),
            version=int(data["version"]),
            title=str(data["title"]),
            hypothesis=str(data["hypothesis"]),
            project=str(data["project"]),
            frozen=bool(data["frozen"]),
            frozen_at=None if data["frozen_at"] is None else str(data["frozen_at"]),
            stored_hash=None if data["definition_hash"] is None else str(data["definition_hash"]),
            primary_metric=primary,
            guardrail_metrics=[dict(item) for item in data["guardrail_metrics"]],
            pass_threshold=float(data["pass_threshold"]),
            failure_threshold=float(data["failure_threshold"]),
            corpus=dict(data["corpus"]),
            knowledge=tuple(str(item) for item in data["knowledge"]),
            raw=data,
        )

    @property
    def corpus_id(self) -> str:
        return str(self.corpus["id"])

    def current_hash(self) -> str:
        return definition_hash(self.raw)

    def hash_matches(self) -> bool:
        return self.stored_hash is not None and self.stored_hash == self.current_hash()

    def summary_row(self) -> dict[str, str | None]:
        return {
            "version": str(self.version),
            "title": self.title,
            "frozen": "true" if self.frozen else "false",
            "corpus": self.corpus_id,
        }


@dataclass(frozen=True)
class ExperimentResult:
    run_id: str
    experiment: str
    experiment_version: int
    definition_hash: str
    verdict: str
    primary_metric_value: float
    guardrail_violations: tuple[str, ...]
    interpretation: str
    evidence: tuple[str, ...]
    raw: dict[str, Any]

    @classmethod
    def from_document(cls, document: Any, *, source: str | None = None) -> ExperimentResult:
        data = dict(RESULT_SCHEMA.validate(document, source=source))
        return cls(
            run_id=str(data["run_id"]),
            experiment=str(data["experiment"]),
            experiment_version=int(data["experiment_version"]),
            definition_hash=str(data["definition_hash"]),
            verdict=str(data["verdict"]),
            primary_metric_value=float(data["primary_metric_value"]),
            guardrail_violations=tuple(str(item) for item in data["guardrail_violations"]),
            interpretation=str(data["interpretation"]),
            evidence=tuple(str(item) for item in data["evidence"]),
            raw=data,
        )


def new_experiment_document(
    *,
    record_id: str,
    title: str,
    hypothesis: str,
    independent_variable: str,
    control: dict[str, str],
    treatment: dict[str, str],
    corpus: dict[str, Any],
    scorer: dict[str, Any],
    primary_metric: dict[str, Any],
    pass_threshold: float,
    failure_threshold: float,
    project: str,
    now: str,
    guardrail_metrics: list[dict[str, Any]] | None = None,
    environment_fingerprint: dict[str, str | None] | None = None,
    tool_versions: dict[str, str | None] | None = None,
    implementation_fingerprint: str | None = None,
    knowledge: tuple[str, ...] = (),
    version: int = 1,
    supersedes_version: int | None = None,
    revision_reason: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": EXPERIMENT_SCHEMA_VERSION,
        "id": record_id,
        "version": version,
        "title": title,
        "hypothesis": hypothesis,
        "independent_variable": independent_variable,
        "control": control,
        "treatment": treatment,
        "corpus": corpus,
        "scorer": scorer,
        "primary_metric": primary_metric,
        "guardrail_metrics": guardrail_metrics or [],
        "pass_threshold": pass_threshold,
        "failure_threshold": failure_threshold,
        "environment_fingerprint": environment_fingerprint or {},
        "tool_versions": tool_versions or {},
        "implementation_fingerprint": implementation_fingerprint,
        "knowledge": sorted(set(knowledge)),
        "project": project,
        "created_at": now,
        "updated_at": now,
        "frozen": False,
        "frozen_at": None,
        "definition_hash": None,
        "supersedes_version": supersedes_version,
        "revision_reason": revision_reason,
    }
