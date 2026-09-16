"""VS6: an evaluation identifies the inputs it was run against.

The gap this closes, measured at ``68eee05``. The runner stamps a report with
the skill's behaviour fingerprint, which lets the ``validated`` gate ask *is
this evaluation still about this skill?* -- and there was no corresponding
question about the corpus. ``corpus_id`` is a free string in a plain file, and
nothing recorded what the evaluation actually consumed. Deleting every negative
case, the guardrail DEC-0003 calls the dangerous direction, left the passing
evidence counting toward promotion with ``doctor`` silent.

**What the digest is.** An *identity and provenance* claim over the evaluation
input set: were the inputs on disk the inputs that produced this evidence? It is
deliberately not a model of scoring semantics. Parsed-content hashing makes
indentation, quoting and key order irrelevant, but no value-level normalization
is applied -- a reordered ``signals`` list is a changed input file, and so a
changed identity, even though ``applies_to`` would score it identically. A false
"not current" costs one deterministic re-run; a false "current" costs a wrong
promotion.

**What it is not.** It is a second, independent dimension beside the behaviour
fingerprint, never folded into it: one names the skill under evaluation, the
other the inputs it was evaluated against. Current evidence needs both.

**History is not a defect.** Legacy and superseded records are immutable and
stay enumerable; they simply cannot satisfy ``current_only``. Once a current
evaluation exists, ``doctor`` is clean again with the old records untouched.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from skillkernel.core.errors import SkillKernelError
from skillkernel.core.paths import Layout
from skillkernel.evaluation.runner import evaluate_skill
from skillkernel.evaluation.suite import evaluation_input_digest, write_evaluation_suite
from skillkernel.evidence.ledger import EvidenceLedger
from skillkernel.promotion.gates import passing_evaluations
from skillkernel.skills.store import SkillStore
from skillkernel.validation.doctor import ERROR, WARNING, run_doctor

ACTIVATION = {"require_any": ["alpha"], "require_all": [], "exclude_any": ["beta"]}
PROJECT = "fixture-project"

POSITIVE = [{"case_id": "p-one", "signals": ["alpha"]}, {"case_id": "p-two", "signals": ["alpha"]}]
NEGATIVE = [{"case_id": "n-one", "signals": ["beta"]}]


def make_subject(layout: Layout, slug: str = "subject") -> Any:
    store = SkillStore(layout)
    record = store.create(
        name=slug.replace("-", " ").title(),
        scope="core",
        slug=slug,
        purpose="A purpose.",
        applies_when=["alpha present"],
        do_not_apply_when=["beta present"],
        activation_rules=ACTIVATION,
    )
    return store.update(
        record.id,
        procedure=["do the thing"],
        success_conditions=["it worked"],
        failure_modes=["it did not"],
        verification=["look at it"],
    )


def author(layout: Layout, skill_id: str, **overrides: Any) -> Any:
    kwargs: dict[str, Any] = {
        "corpus_id": "corpus-one",
        "pass_threshold": 0.5,
        "max_false_activation_rate": 0.5,
        "positive": POSITIVE,
        "negative": NEGATIVE,
    }
    kwargs.update(overrides)
    return write_evaluation_suite(layout, skill_id, **kwargs)


def suite_dir(layout: Layout, slug: str = "subject") -> Path:
    return layout.skills_dir / "core" / slug


def definition_file(layout: Layout, slug: str = "subject") -> Path:
    return suite_dir(layout, slug) / "scorer" / "eval.yaml"


def case_file(layout: Layout, polarity: str, case_id: str, slug: str = "subject") -> Path:
    return suite_dir(layout, slug) / "examples" / polarity / f"{case_id}.yaml"


def edit(path: Path, **changes: Any) -> None:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document.update(changes)
    path.write_text(yaml.safe_dump(document), encoding="utf-8")


def prepared(layout: Layout) -> str:
    """A skill with an authored suite, ready to evaluate."""
    record = make_subject(layout)
    author(layout, record.id)
    return record.id


# --- stability: identity survives rendering ---------------------------------


def test_unchanged_inputs_produce_the_same_digest(layout: Layout) -> None:
    skill_id = prepared(layout)
    assert evaluation_input_digest(layout, skill_id) == evaluation_input_digest(layout, skill_id)


def test_formatting_only_changes_do_not_change_the_digest(layout: Layout) -> None:
    """Parsed-content hashing: rendering is not identity."""
    skill_id = prepared(layout)
    before = evaluation_input_digest(layout, skill_id)

    path = case_file(layout, "positive", "p-one")
    path.write_text(
        "schema_version:   1\n"
        "signals:\n"
        '    - "alpha"\n'
        "case_id: 'p-one'\n"
        "expected: applies\n"
        "description: null\n",
        encoding="utf-8",
    )
    assert evaluation_input_digest(layout, skill_id) == before


def test_crlf_line_endings_do_not_change_the_digest(layout: Layout) -> None:
    skill_id = prepared(layout)
    before = evaluation_input_digest(layout, skill_id)
    path = definition_file(layout)
    path.write_bytes(path.read_text(encoding="utf-8").replace("\n", "\r\n").encode("utf-8"))
    assert evaluation_input_digest(layout, skill_id) == before


@pytest.mark.parametrize("where", ["suite", "case"])
def test_description_edits_do_not_change_the_digest(layout: Layout, where: str) -> None:
    """Documentation carried beside the inputs is not itself an input."""
    skill_id = prepared(layout)
    before = evaluation_input_digest(layout, skill_id)
    target = definition_file(layout) if where == "suite" else case_file(layout, "positive", "p-one")
    edit(target, description="rewritten prose that no consumer reads")
    assert evaluation_input_digest(layout, skill_id) == before


# --- sensitivity: every consumed field is covered ---------------------------


def add_case(layout: Layout) -> None:
    case_file(layout, "positive", "p-three").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "case_id": "p-three",
                "expected": "applies",
                "signals": ["alpha"],
                "description": None,
            }
        ),
        encoding="utf-8",
    )


MUTATIONS = {
    "case added": add_case,
    "case removed": lambda lo: case_file(lo, "positive", "p-two").unlink(),
    "case file renamed": lambda lo: case_file(lo, "positive", "p-one").rename(
        case_file(lo, "positive", "zz-renamed")
    ),
    "case_id changed": lambda lo: edit(case_file(lo, "positive", "p-one"), case_id="p-other"),
    "expected flipped": lambda lo: edit(
        case_file(lo, "positive", "p-one"), expected="does_not_apply"
    ),
    "signals changed": lambda lo: edit(case_file(lo, "positive", "p-one"), signals=["gamma"]),
    "signals reordered": lambda lo: edit(
        case_file(lo, "negative", "n-one"), signals=["beta", "delta"]
    ),
    "pass_threshold changed": lambda lo: edit(definition_file(lo), pass_threshold=0.9),
    "guardrail changed": lambda lo: edit(definition_file(lo), max_false_activation_rate=0.1),
    "scorer_version changed": lambda lo: edit(definition_file(lo), scorer_version="2"),
    "corpus_id changed": lambda lo: edit(definition_file(lo), corpus_id="corpus-two"),
    "suite extension added": lambda lo: edit(definition_file(lo), x_note="carries meaning"),
    "case extension added": lambda lo: edit(
        case_file(lo, "positive", "p-one"), x_note="carries meaning"
    ),
}


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_every_consumed_input_changes_the_digest(layout: Layout, name: str) -> None:
    skill_id = prepared(layout)
    if name == "signals reordered":
        # Seed the ordered pair first, so the mutation is purely a reorder.
        edit(case_file(layout, "negative", "n-one"), signals=["delta", "beta"])
    before = evaluation_input_digest(layout, skill_id)
    MUTATIONS[name](layout)
    assert evaluation_input_digest(layout, skill_id) != before, f"{name} left the digest unchanged"


def test_a_suite_claiming_another_skill_is_refused_not_digested(layout: Layout) -> None:
    """``skill`` is in the identity field set, but it can never vary in practice.

    ``_read_inputs`` refuses a definition that names a different skill, so the
    mismatch fails closed before a digest exists rather than producing a
    different one. Asserted here so the field's presence in the set is not
    mistaken for coverage it never exercises.
    """
    skill_id = prepared(layout)
    edit(definition_file(layout), skill="SKILL-0099")
    with pytest.raises(SkillKernelError, match="declares skill"):
        evaluation_input_digest(layout, skill_id)


def test_a_signals_reorder_changes_identity_even_though_scoring_is_unaffected(
    layout: Layout,
) -> None:
    """Documented consequence of the identity framing, asserted rather than implied.

    ``applies_to`` builds a set, so the verdict is identical either way. The
    digest still changes, because the input file changed and this is an identity
    claim, not a second model of the scorer.
    """
    skill_id = prepared(layout)
    edit(case_file(layout, "negative", "n-one"), signals=["delta", "beta"])
    first = evaluation_input_digest(layout, skill_id)
    before_verdict = evaluate_skill(layout, skill_id, project=PROJECT, record_evidence=False)

    edit(case_file(layout, "negative", "n-one"), signals=["beta", "delta"])
    assert evaluation_input_digest(layout, skill_id) != first
    after_verdict = evaluate_skill(layout, skill_id, project=PROJECT, record_evidence=False)
    assert after_verdict.verdict == before_verdict.verdict


# --- the gate matrix --------------------------------------------------------


def test_matching_digest_and_fingerprint_counts_as_current(layout: Layout) -> None:
    skill_id = prepared(layout)
    report = evaluate_skill(layout, skill_id, project=PROJECT)
    assert report.verdict == "pass"
    skill = SkillStore(layout).get(skill_id)
    assert [record.id for record in passing_evaluations(layout, skill)] == [report.evidence_id]


def test_a_mutated_corpus_stops_counting_as_current(layout: Layout) -> None:
    """The demonstrated exploit: delete the guardrail corpus, keep the evidence."""
    skill_id = prepared(layout)
    evaluate_skill(layout, skill_id, project=PROJECT)
    case_file(layout, "negative", "n-one").unlink()
    add_case(layout)

    skill = SkillStore(layout).get(skill_id)
    assert passing_evaluations(layout, skill) == []
    assert passing_evaluations(layout, skill, current_only=False) != []


def test_a_stale_fingerprint_still_disqualifies_a_matching_digest(layout: Layout) -> None:
    skill_id = prepared(layout)
    evaluate_skill(layout, skill_id, project=PROJECT)
    store = SkillStore(layout)
    store.update(skill_id, procedure=["a different procedure entirely"])

    skill = store.get(skill_id)
    assert evaluation_input_digest(layout, skill_id) is not None
    assert passing_evaluations(layout, skill) == []


def test_history_remains_enumerable_when_nothing_is_current(layout: Layout) -> None:
    skill_id = prepared(layout)
    report = evaluate_skill(layout, skill_id, project=PROJECT)
    edit(definition_file(layout), corpus_id="corpus-two")
    skill = SkillStore(layout).get(skill_id)
    assert passing_evaluations(layout, skill) == []
    assert [r.id for r in passing_evaluations(layout, skill, current_only=False)] == [
        report.evidence_id
    ]


# --- fail closed ------------------------------------------------------------


def _remove_every_case(layout: Layout) -> None:
    for path in (suite_dir(layout) / "examples").rglob("*.yaml"):
        path.unlink()


BREAKAGES = {
    "definition deleted": lambda lo: definition_file(lo).unlink(),
    "definition malformed": lambda lo: definition_file(lo).write_text(
        "pass_threshold: not-a-number\n", encoding="utf-8"
    ),
    "all cases removed": _remove_every_case,
    "case malformed": lambda lo: case_file(lo, "positive", "p-one").write_text(
        "expected: sideways\n", encoding="utf-8"
    ),
}


@pytest.mark.parametrize("name", sorted(BREAKAGES))
def test_an_uncomputable_digest_is_never_current(layout: Layout, name: str) -> None:
    """No digest means no proof, and no proof means not current."""
    skill_id = prepared(layout)
    evaluate_skill(layout, skill_id, project=PROJECT)
    BREAKAGES[name](layout)

    skill = SkillStore(layout).get(skill_id)
    assert passing_evaluations(layout, skill) == []


@pytest.mark.parametrize("name", sorted(BREAKAGES))
def test_a_broken_suite_is_a_domain_outcome_not_an_internal_error(
    layout: Layout, name: str
) -> None:
    """DEC-0010: the kernel found a problem; it did not itself break."""
    skill_id = prepared(layout)
    evaluate_skill(layout, skill_id, project=PROJECT)
    BREAKAGES[name](layout)

    report = run_doctor(layout)
    assert report.is_complete, report.internal_errors


# --- doctor -----------------------------------------------------------------


def findings_for(layout: Layout, prefix: str = "evaluation-input") -> list[tuple[str, str, str]]:
    report = run_doctor(layout)
    assert report.is_complete, report.internal_errors
    return sorted(
        (finding.severity, finding.code, finding.location)
        for finding in report.findings
        if finding.code.startswith(prefix)
    )


def test_a_mismatch_is_reported(layout: Layout) -> None:
    skill_id = prepared(layout)
    evaluate_skill(layout, skill_id, project=PROJECT)
    case_file(layout, "negative", "n-one").unlink()
    assert findings_for(layout) == [(WARNING, "evaluation-input:mismatch", skill_id)]


def test_an_unreadable_suite_is_reported(layout: Layout) -> None:
    skill_id = prepared(layout)
    evaluate_skill(layout, skill_id, project=PROJECT)
    definition_file(layout).unlink()
    assert findings_for(layout) == [(WARNING, "evaluation-input:unreadable", skill_id)]


def test_a_skill_with_no_evaluation_is_not_a_finding(layout: Layout) -> None:
    make_subject(layout)
    assert findings_for(layout) == []


def test_a_current_evaluation_supersedes_a_stale_one(layout: Layout) -> None:
    """History is not a defect. Re-evaluation restores a clean report."""
    skill_id = prepared(layout)
    first = evaluate_skill(layout, skill_id, project=PROJECT)
    case_file(layout, "negative", "n-one").unlink()
    assert findings_for(layout) != []

    second = evaluate_skill(layout, skill_id, project=PROJECT)
    assert second.evidence_id != first.evidence_id
    assert findings_for(layout) == []
    skill = SkillStore(layout).get(skill_id)
    assert [r.id for r in passing_evaluations(layout, skill)] == [second.evidence_id]


def test_severity_rises_when_maturity_rests_on_the_evidence(layout: Layout) -> None:
    """A stale corpus under a promoted skill is an error, not a nudge."""
    skill_id = prepared(layout)
    evaluate_skill(layout, skill_id, project=PROJECT)
    store = SkillStore(layout)
    previous = "observed"
    for state in ("candidate", "experimental", "validated"):
        store.record_transition(
            skill_id,
            previous_state=previous,
            new_state=state,
            reason="test fixture",
            actor="test",
        )
        previous = state

    case_file(layout, "negative", "n-one").unlink()
    assert findings_for(layout) == [(ERROR, "evaluation-input:mismatch", skill_id)]


def test_one_skills_stale_corpus_does_not_hide_anothers(layout: Layout) -> None:
    """VS5's per-skill isolation must survive the new check."""
    first = prepared(layout)
    evaluate_skill(layout, first, project=PROJECT)
    second = make_subject(layout, slug="other").id
    author(layout, second)
    evaluate_skill(layout, second, project=PROJECT)

    case_file(layout, "negative", "n-one").unlink()
    case_file(layout, "negative", "n-one", slug="other").unlink()
    assert findings_for(layout) == [
        (WARNING, "evaluation-input:mismatch", first),
        (WARNING, "evaluation-input:mismatch", second),
    ]


# --- immutability, and v1 reports beside v2 ---------------------------------


def digest_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def artifact_of(layout: Layout, evidence_id: str) -> Path:
    record = EvidenceLedger(layout).get(evidence_id)
    assert record.artifact is not None, f"{evidence_id} has no artifact"
    return layout.root / str(record.artifact["path"])


def test_reevaluation_never_rewrites_earlier_evidence(layout: Layout) -> None:
    skill_id = prepared(layout)
    first = evaluate_skill(layout, skill_id, project=PROJECT)
    assert first.evidence_id is not None
    record_before = EvidenceLedger(layout).registry.path_of(first.evidence_id).read_bytes()
    artifact_before = digest_of(artifact_of(layout, first.evidence_id))

    case_file(layout, "negative", "n-one").unlink()
    evaluate_skill(layout, skill_id, project=PROJECT)

    assert EvidenceLedger(layout).registry.path_of(first.evidence_id).read_bytes() == record_before
    assert digest_of(artifact_of(layout, first.evidence_id)) == artifact_before


def test_a_schema_v1_report_remains_readable_beside_a_v2_report(layout: Layout) -> None:
    """Legacy artifacts stay valid JSON, keep their recorded shape, and verify."""
    skill_id = prepared(layout)
    report = evaluate_skill(layout, skill_id, project=PROJECT)
    assert report.evidence_id is not None
    current = json.loads(artifact_of(layout, report.evidence_id).read_text(encoding="utf-8"))
    assert current["schema_version"] == 2
    assert "evaluation_input_digest" in current

    legacy = {key: value for key, value in current.items() if key != "evaluation_input_digest"}
    legacy["schema_version"] = 1
    stored = layout.evidence_artifacts_dir / "legacy-v1.json"
    stored.parent.mkdir(parents=True, exist_ok=True)
    stored.write_text(json.dumps(legacy, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    reloaded = json.loads(stored.read_text(encoding="utf-8"))
    assert reloaded["schema_version"] == 1
    assert reloaded["skill"] == skill_id
    assert reloaded["verdict"] == "pass"
    assert "evaluation_input_digest" not in reloaded


def test_legacy_evidence_without_a_digest_is_not_current(layout: Layout) -> None:
    """Pre-VS6 evidence: valid history, never proof about today's inputs."""
    skill_id = prepared(layout)
    report = evaluate_skill(layout, skill_id, project=PROJECT)
    assert report.evidence_id is not None

    ledger = EvidenceLedger(layout)
    path = ledger.registry.path_of(report.evidence_id)
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    del document["attributes"]["evaluation_input_digest"]
    path.write_text(yaml.safe_dump(document), encoding="utf-8")

    skill = SkillStore(layout).get(skill_id)
    assert passing_evaluations(layout, skill) == []
    assert passing_evaluations(layout, skill, current_only=False) != []


def test_legacy_evidence_is_reported_then_superseded(layout: Layout) -> None:
    skill_id = prepared(layout)
    report = evaluate_skill(layout, skill_id, project=PROJECT)
    assert report.evidence_id is not None
    ledger = EvidenceLedger(layout)
    path = ledger.registry.path_of(report.evidence_id)
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    del document["attributes"]["evaluation_input_digest"]
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    legacy_bytes = path.read_bytes()

    assert findings_for(layout) == [(WARNING, "evaluation-input:legacy", skill_id)]

    evaluate_skill(layout, skill_id, project=PROJECT)
    assert findings_for(layout) == []
    assert path.read_bytes() == legacy_bytes


def test_the_digest_is_not_stored_in_the_suite_definition(layout: Layout) -> None:
    """Storing it beside the inputs would make it self-referential and editable."""
    skill_id = prepared(layout)
    evaluate_skill(layout, skill_id, project=PROJECT)
    document = yaml.safe_load(definition_file(layout).read_text(encoding="utf-8"))
    assert not any("digest" in key for key in document)


def test_an_unknown_skill_raises_rather_than_reporting_a_digest(layout: Layout) -> None:
    with pytest.raises(SkillKernelError):
        evaluation_input_digest(layout, "SKILL-9999")
