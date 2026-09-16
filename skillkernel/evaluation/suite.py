"""Evaluation definitions and their labelled example cases.

An evaluation suite lives inside the skill's own directory:

    <skill>/scorer/eval.yaml          the definition: corpus, thresholds
    <skill>/examples/positive/*.yaml  cases where the skill SHOULD activate
    <skill>/examples/negative/*.yaml  cases where it SHOULD NOT

Both directions are required. A suite with only positive cases can measure
whether a skill fires but never whether it fires when it should not, and
over-activation is the dangerous direction.

The definition *names* its cases. A suite is exactly the files its ``cases``
manifest lists -- not whatever the examples directories happen to hold -- and
writing that definition is the only moment a reader's view of the suite changes
(DEC-0021). Discovering cases by globbing made physical presence confer
participation, which is the mirror image of the rule DEC-0018 states for
ownership, and left multi-file replacement with no commit point.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from skillkernel.core.errors import ValidationError
from skillkernel.core.ids import SKILL
from skillkernel.core.paths import Layout, is_canonical_case_id, validate_case_id
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
    "CASE_SCHEMA_VERSION",
    "DEFINITION_SCHEMA_VERSION",
    "EVAL_DEFINITION_SCHEMA",
    "EvaluationCase",
    "EvaluationSuite",
    "case_manifest_entry",
    "evaluation_input_digest",
    "load_evaluation_suite",
    "upgrade_definition_to_manifest",
    "write_evaluation_suite",
]

CASE_SCHEMA_VERSION = 1
"""The case document format. Unchanged by manifest authority.

Versioned separately from the definition because the two documents change for
different reasons. They shared one constant, so bumping the definition would
have invalidated every case file ever written -- a coupling with no meaning
behind it.
"""

LEGACY_DEFINITION_SCHEMA_VERSION = 1
"""The definition format that discovered its cases by globbing.

Still read, so an existing workspace keeps working. Never written: the first
authoring pass over such a suite upgrades it.
"""

DEFINITION_SCHEMA_VERSION = 2
"""The definition format that names its cases."""

POLARITIES = ("positive", "negative")
"""The two example directories, in the order a suite lists them."""

CASE_SUFFIX = ".yaml"
"""The one extension a case file may have.

A single spelling is what makes ``a-one.yaml`` and ``a-one.yml`` distinguishable
as *different* files rather than two ways of saying the same case.
"""

EXPECTATIONS = ("applies", "does_not_apply")
SCORER_NAME = "activation-boundary"
SCORER_VERSION = "1"

EVAL_DEFINITION_SCHEMA = Schema(
    name="evaluation-definition",
    supported_versions=(LEGACY_DEFINITION_SCHEMA_VERSION, DEFINITION_SCHEMA_VERSION),
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
            "cases": list_spec(
                str_spec(min_length=1),
                description=(
                    "Every case file this suite comprises, as "
                    "examples/<polarity>/<case-id>.yaml relative to the skill directory. "
                    "Required from schema_version 2; absent before it."
                ),
            ),
        },
        unknown="allow_extension",
    ),
)

CASE_SCHEMA = Schema(
    name="evaluation-case",
    supported_versions=(CASE_SCHEMA_VERSION,),
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

SUITE_SCORING_FIELDS = ("pass_threshold", "max_false_activation_rate")
"""Definition fields the scorer and the verdict actually read."""

DEFINITION_ENCODING_FIELDS = ("schema_version", "cases")
"""Definition fields that describe the encoding, not the input. Not in identity.

``schema_version`` says how to read the document and ``cases`` says which files
to read; neither is itself an input. Excluding them is what makes the upgrade
from a globbing definition to the manifest that names exactly the same files a
no-op for evidence: same corpus, same digest, same standing.

They are not a hole in coverage, because the thing they determine is covered
directly. Every consumed file is framed into the digest by its own path, so a
manifest that named a different set, or a version that made the loader read a
different set, would move the digest through the *files*. What is deliberately
not covered is a hypothetical future version that reinterprets a value this one
already reads -- that would need its own migration, not a silent bump.
"""

SUITE_PROVENANCE_FIELDS = ("skill", "corpus_id", "scorer", "scorer_version")
"""Definition fields that identify the run rather than decide it.

None of these reach ``score_activation``. They change what the evidence is a
claim *about*: which skill, which corpus, which scorer. ``corpus_id`` in
particular is what ``trusted`` counts, so the label must never be able to drift
away from the content it names.
"""

CASE_SCORING_FIELDS = ("schema_version", "case_id", "expected", "signals")
"""Case fields the scorer reads. The file's path is its provenance."""

CORPUS_CONTENT_ALGORITHM_ID = b"skillkernel-corpus-content-v1\x00"
"""Domain separator for corpus content identity.

Deliberately distinct from the input digest, because the two answer different
questions. The input digest identifies *the live input state* -- every consumed
file, keyed by path, scoring fields and provenance together. This identifies
*the case set*: cases only, keyed by ``case_id``, scoring content only.

So a renamed file, a retuned threshold, a bumped ``scorer_version`` and above all
a rewritten ``corpus_id`` all leave corpus identity alone, while a changed,
added or removed case does not. ``trusted`` counts distinct corpora, and a label
was never evidence of distinctness.
"""

SNAPSHOT_SCHEMA_VERSION = 1

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


@dataclass(frozen=True)
class EvaluationInputs:
    """The one parsed representation of everything an evaluation consumes.

    Scoring execution, both digests and the immutable snapshot are all derived
    from this single structure, read once. Re-reading the filesystem for any of
    them would let the four disagree about what was evaluated -- which is the
    class of defect this slice exists to remove, not to reproduce.

    :meth:`from_snapshot` rebuilds the same structure from a preserved
    snapshot, so a historical evaluation is verified by exactly the code that
    produced it.
    """

    skill_id: str
    definition_path: str
    definition: dict[str, Any]
    cases: tuple[tuple[str, dict[str, Any]], ...]

    def suite(self) -> EvaluationSuite:
        data = self.definition
        return EvaluationSuite(
            skill_id=self.skill_id,
            corpus_id=str(data["corpus_id"]),
            pass_threshold=float(data["pass_threshold"]),
            max_false_activation_rate=float(data["max_false_activation_rate"]),
            scorer=str(data["scorer"]),
            scorer_version=str(data["scorer_version"]),
            cases=tuple(
                EvaluationCase.from_document(document, source=relative)
                for relative, document in self.cases
            ),
        )

    def snapshot_document(self) -> dict[str, Any]:
        """The replayable record of these inputs, for the evidence artifact."""
        return {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "skill": self.skill_id,
            "definition_path": self.definition_path,
            "documents": {self.definition_path: self.definition, **dict(self.cases)},
        }

    @classmethod
    def from_snapshot(cls, document: Mapping[str, Any], *, source: str) -> EvaluationInputs:
        """Rebuild preserved inputs, refusing anything that does not parse."""
        try:
            definition_path = str(document["definition_path"])
            documents = dict(document["documents"])
            definition = dict(documents.pop(definition_path))
            skill_id = str(document["skill"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationError(f"{source} is not a readable evaluation snapshot: {exc}") from exc
        return cls(
            skill_id=skill_id,
            definition_path=definition_path,
            definition=definition,
            cases=tuple((str(key), dict(value)) for key, value in sorted(documents.items())),
        )


def read_evaluation_inputs(layout: Layout, skill_id: str) -> EvaluationInputs:
    """Read and validate every file this skill's evaluation would consume."""
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

    source = layout.relative(path)
    cases: list[tuple[str, dict[str, Any]]] = []
    for relative, case_file in _comprised_case_files(layout, skill_dir, data, source=source):
        if not case_file.is_file():
            raise ValidationError(
                f"{source} names {relative}, which is missing or is not a regular file. "
                "A suite is exactly the cases its definition names, so a case that cannot "
                "be read is a refusal, not a smaller corpus."
            )
        document = dict(CASE_SCHEMA.validate(load_yaml_file(case_file), source=str(case_file)))
        cases.append((relative, document))

    if not cases:
        raise ValidationError(f"{skill_id} has an evaluation definition but no example cases")

    return EvaluationInputs(
        skill_id=skill_id,
        definition_path=layout.relative(path),
        definition=data,
        cases=tuple(cases),
    )


def case_manifest_entry(polarity: str, case_id: str) -> str:
    """How one case file is spelled in a definition's ``cases`` manifest.

    Relative to the *skill* directory rather than the repository root, so that
    moving a skill between scopes does not rewrite its suite.
    """
    return f"examples/{polarity}/{case_id}{CASE_SUFFIX}"


def _parse_manifest_entry(entry: object, *, source: str) -> tuple[str, str]:
    """Split one manifest entry into ``(polarity, stem)``, or refuse it.

    Layer A of DEC-0017, applied to a string before any path is built from it.
    The grammar admits exactly one shape, so a traversal, an absolute path, a
    nested directory and a second file extension are all unrepresentable rather
    than constructed and then caught.
    """
    if not isinstance(entry, str):
        raise ValidationError(f"{source} names a case that is not a string: {entry!r}")
    parts = entry.split("/")
    if len(parts) != 3 or parts[0] != "examples" or parts[1] not in POLARITIES:
        raise ValidationError(
            f"{source} names {entry!r}, which is not a case path. A case is named "
            f"examples/<{'|'.join(POLARITIES)}>/<case-id>{CASE_SUFFIX}, relative to the "
            "skill directory. Nested locations are not part of a suite."
        )
    name = parts[2]
    if not name.endswith(CASE_SUFFIX) or len(name) == len(CASE_SUFFIX):
        raise ValidationError(f"{source} names {entry!r}; a case file is <case-id>{CASE_SUFFIX}.")
    return parts[1], validate_case_id(name[: -len(CASE_SUFFIX)])


def _manifested_case_files(
    layout: Layout, skill_dir: Path, entries: Sequence[Any], *, source: str
) -> list[tuple[str, Path]]:
    """Resolve a manifest to ``(repository-relative path, absolute path)`` pairs.

    Returned in canonical order -- positive before negative, then by case id --
    rather than in the order the manifest happens to list them, so the manifest
    is a *set* and reordering it cannot change a digest or a score.
    """
    found: dict[tuple[int, str], tuple[str, Path]] = {}
    for entry in entries:
        polarity, stem = _parse_manifest_entry(entry, source=source)
        # Keyed by the path, not by the ``case_id`` inside the document. The two
        # are not the same thing on the read side: the polarity directories are
        # separate namespaces, and a corpus is allowed to hold ``positive/twin``
        # beside ``negative/twin``.
        key = (POLARITIES.index(polarity), stem)
        if key in found:
            raise ValidationError(
                f"{source} names {entry!r} more than once. Listing one file twice would "
                "make it look like two cases without it being one."
            )
        directory = examples_dir(layout, skill_dir, polarity)
        # Layer B. The grammar above already makes an escape unconstructible; the
        # destination is proved independently so that neither layer is a single
        # point of failure, and so that a symlink -- which no grammar can see
        # through -- is caught by containment.
        path = layout.require_within(directory, directory / f"{stem}{CASE_SUFFIX}")
        found[key] = (layout.relative(path), path)
    return [found[key] for key in sorted(found)]


def _globbed_case_files(layout: Layout, skill_dir: Path) -> list[tuple[str, Path]]:
    """What a legacy definition comprises: every direct case file, discovered.

    Kept exactly as it was so an existing workspace reads unchanged, and used
    once more -- by :func:`upgrade_definition_to_manifest` -- to write down what
    it found.
    """
    found: list[tuple[str, Path]] = []
    for polarity in POLARITIES:
        directory = examples_dir(layout, skill_dir, polarity)
        if not directory.is_dir():
            continue
        for case_file in sorted(directory.glob(f"*{CASE_SUFFIX}")):
            found.append((layout.relative(case_file), case_file))
    return found


def _comprised_case_files(
    layout: Layout, skill_dir: Path, definition: Mapping[str, Any], *, source: str
) -> list[tuple[str, Path]]:
    """The case files this definition comprises, by its own declared version."""
    version = int(definition["schema_version"])
    if version < DEFINITION_SCHEMA_VERSION:
        if "cases" in definition:
            raise ValidationError(
                f"{source} declares schema_version {version}, which discovers its cases, "
                "but carries a 'cases' manifest. Read as declared the manifest would be "
                "ignored and the suite would be whatever the directories hold -- so the "
                f"document says two different things. Declare schema_version "
                f"{DEFINITION_SCHEMA_VERSION} to make the manifest authoritative."
            )
        return _globbed_case_files(layout, skill_dir)
    entries = definition.get("cases")
    if entries is None:
        raise ValidationError(
            f"{source} declares schema_version {version} but has no 'cases' manifest. "
            "A suite is exactly the cases its definition names, and this one names none."
        )
    return _manifested_case_files(layout, skill_dir, entries, source=source)


def upgrade_definition_to_manifest(layout: Layout, skill_id: str) -> bool:
    """Bring a legacy definition under manifest authority. Returns whether it wrote.

    The manifest names exactly the direct case files the legacy loader sees
    right now, so the upgraded suite comprises the same files, loads the same
    cases and has the same identity: no corpus moves, no digest moves, and no
    evidence stops being current (DEC-0021).

    It is one atomic write of one file. Interrupted, the definition is still the
    legacy one and the suite still reads.

    This runs *first* in any authoring pass, because every window that follows
    depends on the definition naming the old set while the new one is written. A
    legacy definition names nothing, so it would adopt the new files as they
    landed -- which is the defect, not a step towards fixing it.
    """
    skill_dir = _skill_dir(layout, skill_id)
    path = definition_path(layout, skill_dir)
    if not path.is_file():
        return False
    data = dict(EVAL_DEFINITION_SCHEMA.validate(load_yaml_file(path), source=str(path)))
    if int(data["schema_version"]) >= DEFINITION_SCHEMA_VERSION:
        return False

    manifest: list[str] = []
    for relative, case_file in _globbed_case_files(layout, skill_dir):
        stem = case_file.name[: -len(CASE_SUFFIX)]
        if not is_canonical_case_id(stem):
            raise ValidationError(
                f"{relative} is loaded by this skill's legacy evaluation suite, but its "
                f"name cannot be written as a manifest entry: {stem!r} is not a canonical "
                "evaluation case id. Rename or remove it first -- dropping it here would "
                "silently change what the suite measures."
            )
        polarity = case_file.parent.name
        manifest.append(case_manifest_entry(polarity, stem))

    upgraded = {**data, "schema_version": DEFINITION_SCHEMA_VERSION, "cases": manifest}
    EVAL_DEFINITION_SCHEMA.validate(upgraded, source=str(path))
    write_yaml_file(path, upgraded, header=_DEFINITION_HEADER)
    return True


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

    Replacement is committed, in four steps (DEC-0021):

    0. validate everything, writing nothing, so a malformed case refuses before
       a single byte of the repository has changed;
    1. upgrade a legacy definition in place, naming exactly what it already
       comprises -- one atomic write that changes nothing observable;
    2. write every case of the new corpus while the definition still names the
       old one, so a reader still sees the old suite, whole;
    3. write the definition naming the new corpus. **This is the commit.**
       Before it a reader sees exactly the old suite; after it, exactly the new
       one. There is no instant at which a reader sees both, or sees the new
       cases under the old corpus's metadata;
    4. retire the case files the new definition no longer names. Housekeeping:
       an unnamed file is already outside the suite, so an interruption here
       leaves the answer unchanged and ``doctor`` reports the leftovers.

    Re-running after an interruption at any step converges on the same result.
    """
    if not positive:
        raise ValidationError("an evaluation suite needs at least one positive case")
    if not negative:
        raise ValidationError(
            "an evaluation suite needs at least one negative case; without one it cannot "
            "measure false activation, which is the dangerous direction"
        )

    skill_dir = _skill_dir(layout, skill_id)

    # Step 0. Plan the whole corpus before touching the repository.
    seen: set[str] = set()
    planned: list[tuple[str, str, dict[str, Any]]] = []
    for polarity, entries, expected in (
        ("positive", positive, "applies"),
        ("negative", negative, "does_not_apply"),
    ):
        for entry in entries:
            # Layer A, at the reusable boundary. This writer is public, so it
            # must not depend on the bundle installer having checked first.
            case_id = validate_case_id(str(entry["case_id"]))
            if case_id in seen:
                raise ValidationError(f"duplicate case_id {case_id!r} in the evaluation suite")
            seen.add(case_id)
            document = {
                "schema_version": CASE_SCHEMA_VERSION,
                "case_id": case_id,
                "expected": expected,
                "signals": list(entry["signals"]),
                "description": entry.get("description"),
            }
            EvaluationCase.from_document(document, source=case_id)
            planned.append((polarity, case_id, document))

    definition = {
        "schema_version": DEFINITION_SCHEMA_VERSION,
        "skill": skill_id,
        "corpus_id": corpus_id,
        "scorer": SCORER_NAME,
        "scorer_version": SCORER_VERSION,
        "pass_threshold": float(pass_threshold),
        "max_false_activation_rate": float(max_false_activation_rate),
        "description": description,
        "cases": [case_manifest_entry(polarity, case_id) for polarity, case_id, _ in planned],
    }
    EVAL_DEFINITION_SCHEMA.validate(definition, source=f"{skill_id} evaluation definition")

    # Step 1. A legacy definition names nothing, so it has to stop globbing
    # before the new cases appear beside the old ones.
    upgrade_definition_to_manifest(layout, skill_id)

    # Step 2. The new corpus lands while the definition still names the old one.
    written: dict[str, set[Path]] = {}
    for polarity, case_id, document in planned:
        directory = examples_dir(layout, skill_dir, polarity)
        directory.mkdir(parents=True, exist_ok=True)
        # Layer B. The grammar above already makes an escape unconstructible,
        # but the destination is guarded on its own so that neither layer is
        # a single point of failure. Guarding the parent is what proved
        # insufficient: containment has to be asserted of the final path.
        destination = layout.require_within(directory, directory / f"{case_id}{CASE_SUFFIX}")
        write_yaml_file(destination, document, header=_CASE_HEADER)
        written.setdefault(polarity, set()).add(destination.resolve())

    # Step 3. The commit.
    write_yaml_file(definition_path(layout, skill_dir), definition, header=_DEFINITION_HEADER)

    # Step 4. Housekeeping, after the answer has already changed.
    _retire_replaced_cases(layout, skill_dir, written)
    return load_evaluation_suite(layout, skill_id)


def _retire_replaced_cases(
    layout: Layout, skill_dir: Path, written: Mapping[str, set[Path]]
) -> None:
    """Remove managed case files this authoring pass did not write.

    Authoring corpus B used to leave corpus A's cases in place, so the suite
    labelled B actually loaded A union B -- measured, and the reason ``trusted``
    could not mean what DEC-0009 says. Writing a corpus now yields that corpus.

    Deliberately narrow. Only direct ``*.yaml`` files in the two managed
    polarity directories are considered, never recursively: nested directories,
    non-YAML files and every other deferred question on disk are left exactly as
    they are.

    Runs after the commit, and is not part of it. By the time it runs the
    definition already names the new corpus, so these files are outside the
    suite whether or not they are still on disk; removing them tidies the
    directory rather than changing any answer. Interrupted, ``doctor`` reports
    what is left as unnamed case-shaped content and re-running finishes the job.

    Each removal is proved to be inside the directory that owns it (DEC-0017)
    before it happens; this is the only deletion the kernel performs.
    """
    for polarity in ("positive", "negative"):
        directory = examples_dir(layout, skill_dir, polarity)
        if not directory.is_dir():
            continue
        keep = written.get(polarity, set())
        for existing in sorted(directory.glob(f"*{CASE_SUFFIX}")):
            if not existing.is_file() or existing.resolve() in keep:
                continue
            layout.require_within(directory, existing).unlink()


def _digest(algorithm: bytes, framed: Mapping[str, bytes]) -> str:
    body = b"".join(_frame(key, framed[key]) for key in sorted(framed))
    return f"sha256:{sha256_bytes(algorithm + len(framed).to_bytes(8, 'big') + body)}"


def evaluation_input_digest(layout: Layout, skill_id: str) -> str:
    """Identity of the evaluation inputs currently on disk for this skill."""
    return input_digest(read_evaluation_inputs(layout, skill_id))


def input_digest(inputs: EvaluationInputs) -> str:
    """Identity of one parsed input state: every file, keyed by its path.

    An *identity and provenance* claim, not a model of scoring semantics.
    Hashing parsed content makes indentation, quoting, key order and line
    endings irrelevant, but no value-level normalization is applied: a reordered
    ``signals`` list changes the digest even though ``applies_to`` builds a set
    and would score it identically. Re-implementing the scorer's notion of
    equivalence here would put the same semantics in two places, and the costs
    are lopsided -- a false "not current" costs one deterministic re-run, a
    false "current" costs a wrong promotion.
    """
    framed = {
        inputs.definition_path: canonical_json(
            _identity_fields(inputs.definition, (*SUITE_SCORING_FIELDS, *SUITE_PROVENANCE_FIELDS))
        )
    }
    for relative, document in inputs.cases:
        framed[relative] = canonical_json(_identity_fields(document, CASE_SCORING_FIELDS))
    return _digest(EVALUATION_INPUT_ALGORITHM_ID, framed)


def corpus_content_digest(inputs: EvaluationInputs) -> str:
    """Identity of the case set alone, keyed by ``case_id``.

    What ``trusted`` counts. The definition is excluded entirely: a corpus is
    its cases, so retuning a threshold, bumping ``scorer_version`` or rewriting
    ``corpus_id`` does not make a second corpus, and re-filing the same cases
    under different filenames does not either. Two evaluations are independent
    confirmation only when the cases genuinely differ.
    """
    framed = {
        str(document["case_id"]): canonical_json(_identity_fields(document, CASE_SCORING_FIELDS))
        for _relative, document in inputs.cases
    }
    return _digest(CORPUS_CONTENT_ALGORITHM_ID, framed)


def load_evaluation_suite(layout: Layout, skill_id: str) -> EvaluationSuite:
    """Load a skill's evaluation suite, raising if it has none."""
    return read_evaluation_inputs(layout, skill_id).suite()
