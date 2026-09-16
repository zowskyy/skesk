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
from skillkernel.core.paths import Layout, validate_case_id
from skillkernel.core.schema import (
    EXTENSION_PREFIX,
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
from skillkernel.utils.hashing import canonical_json, sha256_bytes

__all__ = [
    "CASE_SCHEMA",
    "EVAL_DEFINITION_SCHEMA",
    "EvaluationCase",
    "EvaluationSuite",
    "evaluation_input_digest",
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

EVALUATION_INPUT_ALGORITHM_ID = b"skillkernel-evaluation-input-v1\x00"
"""Domain separator for the evaluation-input digest.

Distinct from the bundle content hash by construction. The framing technique is
shared -- length-prefixed, domain-separated, over canonical JSON of parsed
content -- but the bundle hash's *coverage* definition is not: it enumerates
packaged files, and this enumerates what an evaluation actually consumed.
"""

SUITE_SCORING_FIELDS = ("schema_version", "pass_threshold", "max_false_activation_rate")
"""Definition fields the scorer and the verdict actually read."""

SUITE_PROVENANCE_FIELDS = ("skill", "corpus_id", "scorer", "scorer_version")
"""Definition fields that identify the run rather than decide it.

None of these reach ``score_activation``. They change what the evidence is a
claim *about*: which skill, which corpus, which scorer. ``corpus_id`` in
particular is what ``trusted`` counts, so the label must never be able to drift
away from the content it names.
"""

CASE_SCORING_FIELDS = ("schema_version", "case_id", "expected", "signals")
"""Case fields the scorer reads. The file's path is its provenance."""

IDENTITY_EXCLUDED_FIELDS = ("description",)
"""Documentation carried beside the inputs, reaching no consumer.

Excluded so that rewriting prose does not invalidate evidence. Everything else,
including unknown ``x_`` extensions, is covered: an extension could carry
meaning this version does not know about, so it fails closed.
"""


def _identity_fields(document: Mapping[str, Any], fields: Sequence[str]) -> dict[str, Any]:
    """The subset of a parsed document that participates in its identity."""
    keep = set(fields)
    return {
        key: value
        for key, value in sorted(document.items())
        if (key in keep or key.startswith(EXTENSION_PREFIX)) and key not in IDENTITY_EXCLUDED_FIELDS
    }


def _frame(path: str, payload: bytes) -> bytes:
    """Length-prefixed framing for one input file.

    Fixed-width big-endian lengths make the encoding structurally unambiguous:
    no delimiter can be forged inside a path or a payload, and no two different
    input sets can produce the same byte sequence.
    """
    path_bytes = path.encode("utf-8")
    return (
        len(path_bytes).to_bytes(8, "big") + path_bytes + len(payload).to_bytes(8, "big") + payload
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
            # Layer A, at the reusable boundary. This writer is public, so it
            # must not depend on the bundle installer having checked first.
            case_id = validate_case_id(str(entry["case_id"]))
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
            # Layer B. The grammar above already makes an escape unconstructible,
            # but the destination is guarded on its own so that neither layer is
            # a single point of failure. Guarding the parent is what proved
            # insufficient: containment has to be asserted of the final path.
            destination = layout.require_within(directory, directory / f"{case_id}.yaml")
            write_yaml_file(destination, document, header=_CASE_HEADER)

    write_yaml_file(definition_path(layout, skill_dir), definition, header=_DEFINITION_HEADER)
    return load_evaluation_suite(layout, skill_id)


@dataclass(frozen=True)
class _EvaluationInputs:
    """Every file an evaluation consumes, validated, with its role explicit.

    One enumeration serves both the suite and its digest. Two enumerations would
    be two definitions of "what an evaluation reads", and the digest would
    eventually claim identity over a set the scorer no longer uses.
    """

    definition_path: str
    definition: dict[str, Any]
    cases: tuple[tuple[str, dict[str, Any]], ...]


def _read_inputs(layout: Layout, skill_id: str) -> _EvaluationInputs:
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

    cases: list[tuple[str, dict[str, Any]]] = []
    for polarity in ("positive", "negative"):
        directory = examples_dir(layout, skill_dir, polarity)
        if not directory.is_dir():
            continue
        for case_file in sorted(directory.glob("*.yaml")):
            document = dict(CASE_SCHEMA.validate(load_yaml_file(case_file), source=str(case_file)))
            cases.append((layout.relative(case_file), document))

    if not cases:
        raise ValidationError(f"{skill_id} has an evaluation definition but no example cases")

    return _EvaluationInputs(
        definition_path=layout.relative(path), definition=data, cases=tuple(cases)
    )


def evaluation_input_digest(layout: Layout, skill_id: str) -> str:
    """Identify the evaluation inputs this skill would currently be scored against.

    An *identity and provenance* claim, not a model of scoring semantics: it
    answers "are these the inputs that produced that evidence?". Hashing parsed
    content makes indentation, quoting, key order and line endings irrelevant,
    but no value-level normalization is applied. A reordered ``signals`` list
    therefore changes the digest even though ``applies_to`` builds a set and
    would score it identically -- the file changed, so the identity changed.

    That asymmetry is deliberate. Re-implementing the scorer's notion of
    equivalence here would put the same semantics in two places, and the cost of
    being wrong is lopsided: a false "not current" costs one deterministic
    re-run, a false "current" costs a wrong promotion.

    Raises the same refusals as :func:`load_evaluation_suite`, so a caller that
    cannot obtain a digest has been told why. Callers deciding currentness must
    treat that as *not current* rather than skipping the check.
    """
    inputs = _read_inputs(layout, skill_id)
    framed = {
        inputs.definition_path: canonical_json(
            _identity_fields(inputs.definition, (*SUITE_SCORING_FIELDS, *SUITE_PROVENANCE_FIELDS))
        )
    }
    for relative, document in inputs.cases:
        framed[relative] = canonical_json(_identity_fields(document, CASE_SCORING_FIELDS))

    body = b"".join(_frame(path, framed[path]) for path in sorted(framed))
    digest = sha256_bytes(EVALUATION_INPUT_ALGORITHM_ID + len(framed).to_bytes(8, "big") + body)
    return f"sha256:{digest}"


def load_evaluation_suite(layout: Layout, skill_id: str) -> EvaluationSuite:
    """Load a skill's evaluation suite, raising if it has none."""
    inputs = _read_inputs(layout, skill_id)
    data = inputs.definition
    cases = [
        EvaluationCase.from_document(document, source=relative)
        for relative, document in inputs.cases
    ]
    return EvaluationSuite(
        skill_id=skill_id,
        corpus_id=str(data["corpus_id"]),
        pass_threshold=float(data["pass_threshold"]),
        max_false_activation_rate=float(data["max_false_activation_rate"]),
        scorer=str(data["scorer"]),
        scorer_version=str(data["scorer_version"]),
        cases=tuple(cases),
    )
