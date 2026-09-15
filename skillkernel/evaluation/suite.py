"""Evaluation definitions and their labelled example cases.

An evaluation suite lives inside the skill's own directory:

    <skill>/scorer/eval.yaml          the definition: corpus, thresholds
    <skill>/examples/positive/*.yaml  cases where the skill SHOULD activate
    <skill>/examples/negative/*.yaml  cases where it SHOULD NOT

Both directions are required. A suite with only positive cases can measure
whether a skill fires but never whether it fires when it should not, and
over-activation is the dangerous direction.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from skillkernel.core.errors import ValidationError
from skillkernel.core.ids import SKILL
from skillkernel.core.paths import Layout
from skillkernel.core.schema import (
    Schema,
    enum_spec,
    id_spec,
    int_spec,
    list_spec,
    number_spec,
    object_spec,
    str_spec,
)
from skillkernel.core.yamlio import load_yaml_file, write_yaml_file

__all__ = [
    "CASE_SCHEMA",
    "EVAL_DEFINITION_SCHEMA",
    "EvaluationCase",
    "EvaluationSuite",
    "load_evaluation_suite",
    "write_evaluation_suite",
]

SUITE_SCHEMA_VERSION = 1

EXPECTATIONS = ("applies", "does_not_apply")
SCORER_NAME = "activation-boundary"
SCORER_VERSION = "1"

EVAL_DEFINITION_SCHEMA = Schema(
    name="evaluation-definition",
    supported_versions=(SUITE_SCHEMA_VERSION,),
    root=object_spec(
        {
            "schema_version": int_spec(required=True),
            "skill": id_spec(SKILL, required=True),
            "corpus_id": str_spec(
                required=True,
                min_length=1,
                description="Names the case set. Distinct corpora are what 'trusted' counts.",
            ),
            "scorer": str_spec(required=True, min_length=1),
            "scorer_version": str_spec(required=True, min_length=1),
            "pass_threshold": number_spec(
                required=True,
                minimum=0.0,
                maximum=1.0,
                description="Minimum overall accuracy required to pass.",
            ),
            "max_false_activation_rate": number_spec(
                required=True,
                minimum=0.0,
                maximum=1.0,
                description="Guardrail: share of negative cases that may wrongly activate.",
            ),
            "description": str_spec(nullable=True),
        },
        unknown="allow_extension",
    ),
)

CASE_SCHEMA = Schema(
    name="evaluation-case",
    supported_versions=(SUITE_SCHEMA_VERSION,),
    root=object_spec(
        {
            "schema_version": int_spec(required=True),
            "case_id": str_spec(required=True, min_length=1),
            "expected": enum_spec(EXPECTATIONS, required=True),
            "signals": list_spec(str_spec(min_length=1), required=True, min_items=1, unique=True),
            "description": str_spec(nullable=True),
        },
        unknown="allow_extension",
    ),
)

_DEFINITION_HEADER = (
    "# SkillKernel evaluation definition. Scores this skill's activation boundaries\n"
    "# against the labelled cases in ../examples/.\n"
)
_CASE_HEADER = "# SkillKernel evaluation case.\n"


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    expected: str
    signals: tuple[str, ...]
    description: str | None

    @classmethod
    def from_document(cls, document: Any, *, source: str) -> EvaluationCase:
        data = dict(CASE_SCHEMA.validate(document, source=source))
        description = data.get("description")
        return cls(
            case_id=str(data["case_id"]),
            expected=str(data["expected"]),
            signals=tuple(str(item) for item in data["signals"]),
            description=None if description is None else str(description),
        )

    @property
    def should_apply(self) -> bool:
        return self.expected == "applies"


@dataclass(frozen=True)
class EvaluationSuite:
    skill_id: str
    corpus_id: str
    pass_threshold: float
    max_false_activation_rate: float
    scorer: str
    scorer_version: str
    cases: tuple[EvaluationCase, ...]

    @property
    def positive_cases(self) -> tuple[EvaluationCase, ...]:
        return tuple(case for case in self.cases if case.should_apply)

    @property
    def negative_cases(self) -> tuple[EvaluationCase, ...]:
        return tuple(case for case in self.cases if not case.should_apply)


def definition_path(layout: Layout, skill_dir: Path) -> Path:
    return layout.require_inside(skill_dir / "scorer" / "eval.yaml")


def examples_dir(layout: Layout, skill_dir: Path, polarity: str) -> Path:
    return layout.require_inside(skill_dir / "examples" / polarity)


def _skill_dir(layout: Layout, skill_id: str) -> Path:
    from skillkernel.skills.store import SkillStore  # local import avoids a cycle

    return SkillStore(layout).skill_dir(skill_id)


def write_evaluation_suite(
    layout: Layout,
    skill_id: str,
    *,
    corpus_id: str,
    pass_threshold: float,
    max_false_activation_rate: float,
    positive: Sequence[Mapping[str, Any]],
    negative: Sequence[Mapping[str, Any]],
    description: str | None = None,
) -> EvaluationSuite:
    """Author an evaluation suite for a skill.

    Both polarities are required: a suite that cannot detect over-activation is
    not an evaluation of an activation boundary.
    """
    if not positive:
        raise ValidationError("an evaluation suite needs at least one positive case")
    if not negative:
        raise ValidationError(
            "an evaluation suite needs at least one negative case; without one it cannot "
            "measure false activation, which is the dangerous direction"
        )

    skill_dir = _skill_dir(layout, skill_id)
    definition = {
        "schema_version": SUITE_SCHEMA_VERSION,
        "skill": skill_id,
        "corpus_id": corpus_id,
        "scorer": SCORER_NAME,
        "scorer_version": SCORER_VERSION,
        "pass_threshold": float(pass_threshold),
        "max_false_activation_rate": float(max_false_activation_rate),
        "description": description,
    }
    EVAL_DEFINITION_SCHEMA.validate(definition, source=f"{skill_id} evaluation definition")

    seen: set[str] = set()
    for polarity, entries, expected in (
        ("positive", positive, "applies"),
        ("negative", negative, "does_not_apply"),
    ):
        directory = examples_dir(layout, skill_dir, polarity)
        directory.mkdir(parents=True, exist_ok=True)
        for entry in entries:
            case_id = str(entry["case_id"])
            if case_id in seen:
                raise ValidationError(f"duplicate case_id {case_id!r} in the evaluation suite")
            seen.add(case_id)
            document = {
                "schema_version": SUITE_SCHEMA_VERSION,
                "case_id": case_id,
                "expected": expected,
                "signals": list(entry["signals"]),
                "description": entry.get("description"),
            }
            EvaluationCase.from_document(document, source=case_id)
            write_yaml_file(directory / f"{case_id}.yaml", document, header=_CASE_HEADER)

    write_yaml_file(definition_path(layout, skill_dir), definition, header=_DEFINITION_HEADER)
    return load_evaluation_suite(layout, skill_id)


def load_evaluation_suite(layout: Layout, skill_id: str) -> EvaluationSuite:
    """Load a skill's evaluation suite, raising if it has none."""
    skill_dir = _skill_dir(layout, skill_id)
    path = definition_path(layout, skill_dir)
    if not path.is_file():
        raise ValidationError(
            f"{skill_id} has no evaluation definition at {layout.relative(path)}; "
            "author one with write_evaluation_suite() before evaluating"
        )
    data = dict(EVAL_DEFINITION_SCHEMA.validate(load_yaml_file(path), source=str(path)))
    if data["skill"] != skill_id:
        raise ValidationError(
            f"{layout.relative(path)} declares skill {data['skill']!r} but belongs to {skill_id}"
        )

    cases: list[EvaluationCase] = []
    for polarity in ("positive", "negative"):
        directory = examples_dir(layout, skill_dir, polarity)
        if not directory.is_dir():
            continue
        for case_file in sorted(directory.glob("*.yaml")):
            cases.append(
                EvaluationCase.from_document(load_yaml_file(case_file), source=str(case_file))
            )

    if not cases:
        raise ValidationError(f"{skill_id} has an evaluation definition but no example cases")

    return EvaluationSuite(
        skill_id=skill_id,
        corpus_id=str(data["corpus_id"]),
        pass_threshold=float(data["pass_threshold"]),
        max_false_activation_rate=float(data["max_false_activation_rate"]),
        scorer=str(data["scorer"]),
        scorer_version=str(data["scorer_version"]),
        cases=tuple(cases),
    )
