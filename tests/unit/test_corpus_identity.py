"""VS6: distinct corpora, proved from preserved inputs rather than from labels.

``trusted`` requires passing evaluations across N distinct corpora. A skill has
one live suite, so requiring every counted evaluation to match it made the
requirement unsatisfiable -- and before VS6 it was satisfiable only because
evidence describing a vanished corpus still counted.

Three concepts, kept apart:

``evaluation_input_digest``
    Identity of the *live* input state, scoring fields and provenance together.
    Answers "is this evidence about the inputs on disk now?" -- ``validated``.

the immutable snapshot
    The exact parsed inputs an evaluation consumed, written with it and never
    rewritten. It is what makes historical evidence independently verifiable
    once the live suite has moved on.

``corpus_content_digest``
    Identity of the *case set*, keyed by ``case_id`` and covering scoring
    content only. Thresholds, ``scorer_version`` and ``corpus_id`` are excluded,
    so relabelling cannot manufacture a second corpus and re-filing the same
    cases cannot either -- ``trusted``.

So ``validated`` asks about now and ``trusted`` asks about accumulated history,
and neither borrows the other's notion of currentness.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml
from skillkernel.core.paths import Layout
from skillkernel.evaluation.runner import evaluate_skill
from skillkernel.evaluation.suite import (
    corpus_content_digest,
    evaluation_input_digest,
    read_evaluation_inputs,
    write_evaluation_suite,
)
from skillkernel.evidence.ledger import EvidenceLedger
from skillkernel.promotion.gates import passing_evaluations, verifiable_evaluations
from skillkernel.skills.store import SkillStore

ACTIVATION = {"require_any": ["alpha"], "require_all": [], "exclude_any": ["beta"]}
PROJECT = "fixture-project"

CORPUS_A = {
    "corpus_id": "corpus-a",
    "positive": [{"case_id": "shared", "signals": ["alpha"]}],
    "negative": [{"case_id": "a-neg", "signals": ["beta"]}],
}
CORPUS_B = {
    "corpus_id": "corpus-b",
    "positive": [{"case_id": "shared", "signals": ["alpha", "extra"]}],
    "negative": [{"case_id": "b-neg", "signals": ["beta"]}],
}


def subject(layout: Layout, slug: str = "subject") -> Any:
    store = SkillStore(layout)
    record = store.create(
        name="Subject",
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


def author(layout: Layout, skill_id: str, corpus: dict[str, Any]) -> Any:
    return write_evaluation_suite(
        layout,
        skill_id,
        corpus_id=corpus["corpus_id"],
        pass_threshold=0.0,
        max_false_activation_rate=1.0,
        positive=corpus["positive"],
        negative=corpus["negative"],
    )


def examples(layout: Layout, slug: str = "subject") -> Path:
    return layout.skills_dir / "core" / slug / "examples"


def case_ids_on_disk(layout: Layout) -> list[str]:
    return sorted(path.stem for path in examples(layout).rglob("*.yaml"))


def artifact_of(layout: Layout, evidence_id: str) -> Path:
    record = EvidenceLedger(layout).get(evidence_id)
    assert record.artifact is not None
    return layout.root / str(record.artifact["path"])


def digest_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- write_evaluation_suite replaces, it does not merge ---------------------


def test_writing_a_second_corpus_replaces_the_live_suite(layout: Layout) -> None:
    """The demonstrated merge bug: corpus B used to load as A union B."""
    skill_id = subject(layout).id
    author(layout, skill_id, CORPUS_A)
    assert case_ids_on_disk(layout) == ["a-neg", "shared"]

    suite = author(layout, skill_id, CORPUS_B)
    assert sorted(case.case_id for case in suite.cases) == ["b-neg", "shared"]
    assert case_ids_on_disk(layout) == ["b-neg", "shared"]
    assert "a-neg" not in case_ids_on_disk(layout)

    document = yaml.safe_load(
        (examples(layout) / "positive" / "shared.yaml").read_text(encoding="utf-8")
    )
    assert document["signals"] == ["alpha", "extra"], "shared should hold B's version"


def test_replacement_leaves_deferred_filesystem_content_alone(layout: Layout) -> None:
    """Bounded deletion: managed direct case files only, nothing else."""
    skill_id = subject(layout).id
    author(layout, skill_id, CORPUS_A)

    notes = examples(layout) / "positive" / "NOTES.md"
    notes.write_text("not a case", encoding="utf-8")
    nested = examples(layout) / "positive" / "nested"
    nested.mkdir()
    (nested / "deferred.yaml").write_text("deferred: true", encoding="utf-8")
    scorer = layout.skills_dir / "core" / "subject" / "scorer"
    keep = scorer / "keep.txt"
    keep.write_text("keep me", encoding="utf-8")

    author(layout, skill_id, CORPUS_B)

    assert notes.read_text(encoding="utf-8") == "not a case"
    assert (nested / "deferred.yaml").read_text(encoding="utf-8") == "deferred: true"
    assert keep.read_text(encoding="utf-8") == "keep me"


# --- corpus content identity ------------------------------------------------


def test_relabelling_a_corpus_does_not_create_a_new_one(layout: Layout) -> None:
    skill_id = subject(layout).id
    author(layout, skill_id, CORPUS_A)
    before = corpus_content_digest(read_evaluation_inputs(layout, skill_id))

    relabelled = {**CORPUS_A, "corpus_id": "corpus-a-renamed"}
    author(layout, skill_id, relabelled)
    inputs = read_evaluation_inputs(layout, skill_id)
    assert corpus_content_digest(inputs) == before, "a label must not be identity"


def test_changing_the_label_alone_still_changes_live_identity(layout: Layout) -> None:
    """The two digests answer different questions and must not move together."""
    skill_id = subject(layout).id
    author(layout, skill_id, CORPUS_A)
    live_before = evaluation_input_digest(layout, skill_id)
    content_before = corpus_content_digest(read_evaluation_inputs(layout, skill_id))

    author(layout, skill_id, {**CORPUS_A, "corpus_id": "renamed"})
    assert evaluation_input_digest(layout, skill_id) != live_before
    assert corpus_content_digest(read_evaluation_inputs(layout, skill_id)) == content_before


def test_different_cases_under_one_label_are_two_corpora(layout: Layout) -> None:
    skill_id = subject(layout).id
    author(layout, skill_id, CORPUS_A)
    first = corpus_content_digest(read_evaluation_inputs(layout, skill_id))
    author(layout, skill_id, {**CORPUS_B, "corpus_id": CORPUS_A["corpus_id"]})
    assert corpus_content_digest(read_evaluation_inputs(layout, skill_id)) != first


def test_thresholds_and_scorer_version_are_not_corpus_content(layout: Layout) -> None:
    """A different bar over the same cases is not a different corpus."""
    skill_id = subject(layout).id
    author(layout, skill_id, CORPUS_A)
    before = corpus_content_digest(read_evaluation_inputs(layout, skill_id))

    definition = layout.skills_dir / "core" / "subject" / "scorer" / "eval.yaml"
    document = yaml.safe_load(definition.read_text(encoding="utf-8"))
    document.update(pass_threshold=0.9, max_false_activation_rate=0.1, scorer_version="2")
    definition.write_text(yaml.safe_dump(document), encoding="utf-8")

    assert corpus_content_digest(read_evaluation_inputs(layout, skill_id)) == before


def test_renaming_a_case_file_is_not_a_new_corpus(layout: Layout) -> None:
    """Content identity is keyed by case_id; where the file sits is provenance."""
    skill_id = subject(layout).id
    author(layout, skill_id, CORPUS_A)
    before = corpus_content_digest(read_evaluation_inputs(layout, skill_id))

    source = examples(layout) / "negative" / "a-neg.yaml"
    source.rename(examples(layout) / "negative" / "zz-refiled.yaml")

    assert corpus_content_digest(read_evaluation_inputs(layout, skill_id)) == before
    assert evaluation_input_digest(layout, skill_id) is not None


# --- snapshots make history verifiable --------------------------------------


def test_an_evaluation_preserves_the_inputs_it_consumed(layout: Layout) -> None:
    skill_id = subject(layout).id
    author(layout, skill_id, CORPUS_A)
    report = evaluate_skill(layout, skill_id, project=PROJECT)
    assert report.evidence_id is not None

    snapshot = json.loads(artifact_of(layout, report.evidence_id).read_text(encoding="utf-8"))
    assert snapshot["corpus_content_digest"] == report.corpus_content_digest
    assert snapshot["evaluation_input_digest"] == report.evaluation_input_digest
    recorded_cases = {
        document["case_id"]
        for document in snapshot["inputs"]["documents"].values()
        if "case_id" in document
    }
    assert recorded_cases == {"shared", "a-neg"}


def test_the_snapshot_survives_live_replacement_byte_identical(layout: Layout) -> None:
    skill_id = subject(layout).id
    author(layout, skill_id, CORPUS_A)
    first = evaluate_skill(layout, skill_id, project=PROJECT)
    assert first.evidence_id is not None
    before = digest_of(artifact_of(layout, first.evidence_id))

    author(layout, skill_id, CORPUS_B)
    evaluate_skill(layout, skill_id, project=PROJECT)

    assert digest_of(artifact_of(layout, first.evidence_id)) == before


# --- the two gate questions -------------------------------------------------


def two_corpora(layout: Layout) -> tuple[str, Any, Any]:
    skill_id = subject(layout).id
    author(layout, skill_id, CORPUS_A)
    first = evaluate_skill(layout, skill_id, project=PROJECT)
    author(layout, skill_id, CORPUS_B)
    second = evaluate_skill(layout, skill_id, project=PROJECT)
    return skill_id, first, second


def test_live_currentness_follows_only_the_live_suite(layout: Layout) -> None:
    skill_id, _first, second = two_corpora(layout)
    skill = SkillStore(layout).get(skill_id)
    assert [r.id for r in passing_evaluations(layout, skill)] == [second.evidence_id]


def test_trusted_counts_both_verifiable_snapshots(layout: Layout) -> None:
    skill_id, first, second = two_corpora(layout)
    skill = SkillStore(layout).get(skill_id)
    counted = verifiable_evaluations(layout, skill)
    assert sorted(r.id for r in counted) == sorted([first.evidence_id, second.evidence_id])
    assert first.corpus_content_digest != second.corpus_content_digest


def test_a_deleted_snapshot_stops_counting_for_trusted(layout: Layout) -> None:
    skill_id, first, _second = two_corpora(layout)
    artifact_of(layout, first.evidence_id).unlink()
    skill = SkillStore(layout).get(skill_id)
    assert [r.id for r in verifiable_evaluations(layout, skill)] == [_second.evidence_id]


def test_modified_snapshot_bytes_stop_counting_for_trusted(layout: Layout) -> None:
    skill_id, first, _second = two_corpora(layout)
    path = artifact_of(layout, first.evidence_id)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["inputs"]["documents"] = {}
    path.write_text(json.dumps(document), encoding="utf-8")

    skill = SkillStore(layout).get(skill_id)
    assert first.evidence_id not in [r.id for r in verifiable_evaluations(layout, skill)]


def test_a_forged_recorded_digest_stops_counting_for_trusted(layout: Layout) -> None:
    """The attribute the gate reads must agree with the preserved inputs."""
    skill_id, first, _second = two_corpora(layout)
    ledger = EvidenceLedger(layout)
    path = ledger.registry.path_of(first.evidence_id)
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["attributes"]["corpus_content_digest"] = "sha256:" + "0" * 64
    path.write_text(yaml.safe_dump(document), encoding="utf-8")

    skill = SkillStore(layout).get(skill_id)
    assert first.evidence_id not in [r.id for r in verifiable_evaluations(layout, skill)]


def test_legacy_evidence_never_counts_for_trusted(layout: Layout) -> None:
    skill_id, first, _second = two_corpora(layout)
    ledger = EvidenceLedger(layout)
    path = ledger.registry.path_of(first.evidence_id)
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["attributes"].pop("corpus_content_digest", None)
    document["attributes"].pop("evaluation_input_digest", None)
    path.write_text(yaml.safe_dump(document), encoding="utf-8")

    skill = SkillStore(layout).get(skill_id)
    assert first.evidence_id not in [r.id for r in verifiable_evaluations(layout, skill)]


def test_stale_live_identity_still_counts_for_trusted(layout: Layout) -> None:
    """The point of the split: A is not live, but A is still verifiable."""
    skill_id, first, second = two_corpora(layout)
    skill = SkillStore(layout).get(skill_id)
    assert first.evidence_id not in [r.id for r in passing_evaluations(layout, skill)]
    assert first.evidence_id in [r.id for r in verifiable_evaluations(layout, skill)]
    assert second.evidence_id in [r.id for r in passing_evaluations(layout, skill)]


def test_a_live_evaluation_with_a_broken_snapshot_splits_the_two_questions(
    layout: Layout,
) -> None:
    """current_only and trusted are asserted independently, on the same record."""
    skill_id = subject(layout).id
    author(layout, skill_id, CORPUS_A)
    report = evaluate_skill(layout, skill_id, project=PROJECT)
    assert report.evidence_id is not None
    artifact_of(layout, report.evidence_id).unlink()

    skill = SkillStore(layout).get(skill_id)
    assert [r.id for r in passing_evaluations(layout, skill)] == [report.evidence_id]
    assert verifiable_evaluations(layout, skill) == []


# --- gaps found by mutation testing -----------------------------------------


def test_live_identity_is_keyed_by_path_not_basename(layout: Layout) -> None:
    """Two polarity directories can hold the same filename.

    Keying the live digest by basename would collapse such a pair into one
    entry, so two genuinely different input states would share an identity. A
    mutation that keyed by basename survived every other test until this one.
    """
    skill_id = subject(layout).id
    author(layout, skill_id, CORPUS_A)

    twin = {
        "schema_version": 1,
        "case_id": "twin-negative",
        "expected": "does_not_apply",
        "signals": ["beta"],
        "description": None,
    }
    (examples(layout) / "negative" / "twin.yaml").write_text(yaml.safe_dump(twin), encoding="utf-8")
    one_side = evaluation_input_digest(layout, skill_id)

    (examples(layout) / "positive" / "twin.yaml").write_text(
        yaml.safe_dump(
            {**twin, "case_id": "twin-positive", "expected": "applies", "signals": ["alpha"]}
        ),
        encoding="utf-8",
    )
    both_sides = evaluation_input_digest(layout, skill_id)
    assert both_sides != one_side, "a same-named file in the other polarity was invisible"


def test_snapshot_bytes_are_verified_independently_of_the_digest(layout: Layout) -> None:
    """Tampering that leaves corpus content alone must still disqualify.

    Rewriting a field the corpus digest does not cover -- here the recorded
    evaluation timestamp -- replays to the *same* digest, so only the ledger's
    artifact hash can catch it. A mutation that skipped that hash check survived
    until this test existed.
    """
    skill_id, first, _second = two_corpora(layout)
    path = artifact_of(layout, first.evidence_id)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["evaluated_at"] = "1999-01-01T00:00:00Z"
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    rebuilt = json.loads(path.read_text(encoding="utf-8"))
    assert rebuilt["corpus_content_digest"] == first.corpus_content_digest, (
        "the tamper must leave corpus content identical, or this proves nothing"
    )

    skill = SkillStore(layout).get(skill_id)
    assert first.evidence_id not in [r.id for r in verifiable_evaluations(layout, skill)]
