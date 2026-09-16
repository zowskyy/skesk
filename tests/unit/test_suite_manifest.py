"""VS8: a suite is exactly the cases its definition names.

Before this slice an evaluation suite was *whatever the examples directories
happened to contain*. Three consequences were measured at ``db11600``:

* multi-file replacement had no commit point, so an interrupted authoring pass
  left readers looking at ``A union B``, or at ``B`` wearing ``A``'s metadata;
* a file planted in a managed examples directory was consumed as a case, so
  physical presence conferred participation (the mirror image of DEC-0018);
* nothing on disk recorded which files an evaluation was supposed to read, so
  nothing could report a file that was not one of them.

The definition now carries a ``cases`` manifest, and writing it is the only
transition that changes what a reader sees. Everything else -- writing the new
cases, retiring the old ones -- happens while readers still see the previous
suite in full.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from skillkernel.core.errors import SkillKernelError, ValidationError
from skillkernel.core.paths import Layout
from skillkernel.evaluation import suite as suite_module
from skillkernel.evaluation.runner import evaluate_skill
from skillkernel.evaluation.suite import (
    corpus_content_digest,
    evaluation_input_digest,
    load_evaluation_suite,
    read_evaluation_inputs,
    write_evaluation_suite,
)
from skillkernel.promotion.gates import passing_evaluations
from skillkernel.skills.store import SkillStore
from skillkernel.validation.doctor import run_doctor

ACTIVATION = {"require_any": ["alpha"], "require_all": [], "exclude_any": ["beta"]}
PROJECT = "fixture-project"

CORPUS_A = {
    "corpus_id": "corpus-a",
    "positive": [{"case_id": "a-one", "signals": ["alpha"]}],
    "negative": [{"case_id": "a-neg", "signals": ["beta"]}],
}
CORPUS_B = {
    "corpus_id": "corpus-b",
    "positive": [{"case_id": "b-one", "signals": ["alpha"]}],
    "negative": [{"case_id": "b-neg", "signals": ["beta"]}],
}

A_CASES = ["a-neg", "a-one"]
B_CASES = ["b-neg", "b-one"]


# --- fixtures and helpers ---------------------------------------------------


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


def skill_dir(layout: Layout, slug: str = "subject") -> Path:
    return layout.skills_dir / "core" / slug


def examples(layout: Layout, slug: str = "subject") -> Path:
    return skill_dir(layout, slug) / "examples"


def definition_file(layout: Layout, slug: str = "subject") -> Path:
    return skill_dir(layout, slug) / "scorer" / "eval.yaml"


def definition_document(layout: Layout, slug: str = "subject") -> dict[str, Any]:
    return dict(yaml.safe_load(definition_file(layout, slug).read_text(encoding="utf-8")))


def rewrite_definition(layout: Layout, document: dict[str, Any], slug: str = "subject") -> None:
    definition_file(layout, slug).write_text(yaml.safe_dump(document), encoding="utf-8")


def downgrade(layout: Layout, slug: str = "subject") -> dict[str, Any]:
    """Put a legacy schema-v1 definition on disk: no manifest, version 1.

    This is the state every workspace authored before VS8 is in. Constructing it
    by editing a freshly authored definition keeps the fixture honest: it is the
    same document, minus exactly the manifest.
    """
    document = definition_document(layout, slug)
    document.pop("cases", None)
    document["schema_version"] = 1
    rewrite_definition(layout, document, slug)
    return document


def loaded_case_ids(layout: Layout, skill_id: str) -> list[str]:
    return sorted(case.case_id for case in load_evaluation_suite(layout, skill_id).cases)


def files_on_disk(layout: Layout, slug: str = "subject") -> list[str]:
    return sorted(path.stem for path in examples(layout, slug).rglob("*.yaml"))


def findings(layout: Layout) -> list[tuple[str, str, str]]:
    return [(f.severity, f.code, f.location) for f in run_doctor(layout).sorted_findings()]


def case_document(case_id: str, expected: str = "applies") -> dict[str, Any]:
    return {
        "schema_version": 1,
        "case_id": case_id,
        "expected": expected,
        "signals": ["alpha"],
        "description": None,
    }


class Interrupted(Exception):
    """Stands in for power loss: raised from inside an authoring pass."""


def relative(layout: Layout, path: Path) -> str:
    """``path`` relative to the repository root, without resolving its leaf.

    Resolving the leaf would make a symlink indistinguishable from its target,
    which is exactly the difference these tests are about.
    """
    return (path.parent.resolve() / path.name).relative_to(layout.root.resolve()).as_posix()


def intercept(
    monkeypatch: pytest.MonkeyPatch,
    layout: Layout,
    *,
    before: str | None = None,
    after: str | None = None,
) -> list[str]:
    """Detonate an authoring pass at a chosen write.

    ``before`` and ``after`` are path suffixes. The *first* write matching
    either one aborts the pass -- in place of the write, or once it has landed.
    The returned list accumulates every write that was attempted, in order, so a
    test can assert where it stopped rather than trusting that it did.
    """
    real = suite_module.write_yaml_file
    seen: list[str] = []

    def writer(path: Path, data: Any, **kwargs: Any) -> None:
        name = relative(layout, Path(path))
        seen.append(name)
        if before is not None and name.endswith(before):
            raise Interrupted(name)
        real(Path(path), data, **kwargs)
        if after is not None and name.endswith(after):
            raise Interrupted(name)

    monkeypatch.setattr(suite_module, "write_yaml_file", writer)
    return seen


# --- the manifest exists and says what it means -----------------------------


def test_authoring_records_the_cases_it_wrote(layout: Layout) -> None:
    skill_id = subject(layout).id
    author(layout, skill_id, CORPUS_A)

    document = definition_document(layout)
    assert document["schema_version"] == 2
    assert document["cases"] == [
        "examples/positive/a-one.yaml",
        "examples/negative/a-neg.yaml",
    ]


def test_the_manifest_is_a_set_not_an_order(layout: Layout) -> None:
    """Reordering the manifest changes nothing a consumer can observe."""
    skill_id = subject(layout).id
    author(layout, skill_id, CORPUS_A)
    before_inputs = read_evaluation_inputs(layout, skill_id)

    document = definition_document(layout)
    document["cases"] = list(reversed(document["cases"]))
    rewrite_definition(layout, document)

    after_inputs = read_evaluation_inputs(layout, skill_id)
    assert after_inputs.cases == before_inputs.cases
    assert evaluation_input_digest(layout, skill_id) == suite_module.input_digest(before_inputs)


def test_a_duplicated_manifest_entry_is_refused(layout: Layout) -> None:
    skill_id = subject(layout).id
    author(layout, skill_id, CORPUS_A)

    document = definition_document(layout)
    document["cases"] = [*document["cases"], document["cases"][0]]
    rewrite_definition(layout, document)

    with pytest.raises(ValidationError, match="more than once"):
        load_evaluation_suite(layout, skill_id)


# --- only what is named is loaded -------------------------------------------


def test_an_unnamed_direct_case_file_is_not_loaded(layout: Layout) -> None:
    """The owned-state contamination case, closed by construction.

    A file planted in a managed examples directory used to become part of the
    suite. Presence is not participation: the definition names the cases.
    """
    skill_id = subject(layout).id
    author(layout, skill_id, CORPUS_A)

    (examples(layout) / "positive" / "planted.yaml").write_text(
        yaml.safe_dump(case_document("planted")), encoding="utf-8"
    )

    assert loaded_case_ids(layout, skill_id) == A_CASES


def test_a_named_case_that_is_missing_fails_closed(layout: Layout) -> None:
    skill_id = subject(layout).id
    author(layout, skill_id, CORPUS_A)
    (examples(layout) / "positive" / "a-one.yaml").unlink()

    with pytest.raises(ValidationError, match=re.escape("a-one.yaml")):
        load_evaluation_suite(layout, skill_id)


def test_a_named_case_that_is_unreadable_fails_closed(layout: Layout) -> None:
    skill_id = subject(layout).id
    author(layout, skill_id, CORPUS_A)
    (examples(layout) / "positive" / "a-one.yaml").write_text("{ not: [yaml", encoding="utf-8")

    with pytest.raises(SkillKernelError):
        load_evaluation_suite(layout, skill_id)


def test_a_named_case_that_is_invalid_fails_closed(layout: Layout) -> None:
    skill_id = subject(layout).id
    author(layout, skill_id, CORPUS_A)
    broken = case_document("a-one")
    broken.pop("signals")
    (examples(layout) / "positive" / "a-one.yaml").write_text(
        yaml.safe_dump(broken), encoding="utf-8"
    )

    with pytest.raises(ValidationError):
        load_evaluation_suite(layout, skill_id)


def test_a_named_case_that_is_a_directory_fails_closed(layout: Layout) -> None:
    skill_id = subject(layout).id
    author(layout, skill_id, CORPUS_A)
    target = examples(layout) / "positive" / "a-one.yaml"
    target.unlink()
    target.mkdir()

    with pytest.raises(ValidationError, match=re.escape("a-one.yaml")):
        load_evaluation_suite(layout, skill_id)


# Each entry is refused *as a string*, and the message says why. Asserting only
# that "something raised" would be satisfied by the named file simply being
# absent, which is a different refusal -- and a mutation that dropped the
# grammar check survived exactly that weaker assertion.
MALFORMED_ENTRIES = {
    "../../../etc/passwd": "not a case path",
    "/etc/passwd": "not a case path",
    "examples/positive/../../../etc/passwd": "not a case path",
    "examples/positive/../negative/a-neg.yaml": "not a case path",
    "examples/positive/nested/a-one.yaml": "not a case path",
    "examples/nested/positive/a-one.yaml": "not a case path",
    "examples/other/a-one.yaml": "not a case path",
    "scorer/eval.yaml": "not a case path",
    # Caught one layer earlier, by the schema's own min_length.
    "": "must contain at least 1 character",
    "examples/positive/a-one": "a case file is",
    "examples/positive/a-one.yml": "a case file is",
    "examples/positive/": "a case file is",
    "examples/positive/.yaml": "a case file is",
    "examples/positive/A-One.yaml": "not a canonical evaluation case id",
    "examples/positive/a_one.yaml": "not a canonical evaluation case id",
    "examples/positive/-a-one.yaml": "not a canonical evaluation case id",
}


@pytest.mark.parametrize("entry", sorted(MALFORMED_ENTRIES))
def test_a_non_canonical_manifest_entry_is_refused(layout: Layout, entry: str) -> None:
    """Layer A: the entry is rejected as a string, before any path is built."""
    skill_id = subject(layout).id
    author(layout, skill_id, CORPUS_A)

    document = definition_document(layout)
    document["cases"] = [entry]
    rewrite_definition(layout, document)

    with pytest.raises(ValidationError, match=re.escape(MALFORMED_ENTRIES[entry])):
        load_evaluation_suite(layout, skill_id)


@pytest.mark.parametrize(
    "relative",
    [
        "examples/positive/A-One.yaml",
        "examples/positive/a_one.yaml",
        "examples/other/a-one.yaml",
        "examples/positive/nested/a-one.yaml",
        "examples/positive/a-one.yml",
    ],
)
def test_a_malformed_entry_is_refused_even_when_the_file_is_really_there(
    layout: Layout, relative: str
) -> None:
    """The refusal is the grammar, not the filesystem.

    Each of these paths exists, holds a valid case document, and sits inside the
    skill directory, so nothing downstream would object to reading it. It is
    refused because a manifest cannot name it -- which is the whole point of
    checking the entry before building a path from it.
    """
    skill_id = subject(layout).id
    author(layout, skill_id, CORPUS_A)

    planted = skill_dir(layout) / relative
    planted.parent.mkdir(parents=True, exist_ok=True)
    planted.write_text(yaml.safe_dump(case_document("a-one")), encoding="utf-8")

    document = definition_document(layout)
    document["cases"] = [relative]
    rewrite_definition(layout, document)

    with pytest.raises(ValidationError) as refusal:
        load_evaluation_suite(layout, skill_id)
    assert "missing" not in str(refusal.value), (
        "the file is present, so a 'missing case' refusal would mean the entry "
        "was accepted by the grammar and only failed at the filesystem"
    )


def test_a_manifest_entry_pointing_out_of_its_directory_is_refused(layout: Layout) -> None:
    """Layer B: a symlink is a path the grammar cannot see through."""
    skill_id = subject(layout).id
    author(layout, skill_id, CORPUS_A)

    outside = layout.root / "outside.yaml"
    outside.write_text(yaml.safe_dump(case_document("a-one")), encoding="utf-8")
    target = examples(layout) / "positive" / "a-one.yaml"
    target.unlink()
    target.symlink_to(outside)

    with pytest.raises(SkillKernelError):
        load_evaluation_suite(layout, skill_id)


# --- schema v1 still reads, and upgrading it changes nothing ----------------


def test_a_legacy_definition_still_loads_by_globbing(layout: Layout) -> None:
    skill_id = subject(layout).id
    author(layout, skill_id, CORPUS_A)
    downgrade(layout)

    assert loaded_case_ids(layout, skill_id) == A_CASES


def test_a_legacy_definition_may_not_carry_a_manifest(layout: Layout) -> None:
    """A v1 document with a ``cases`` key would have it silently ignored."""
    skill_id = subject(layout).id
    author(layout, skill_id, CORPUS_A)
    document = definition_document(layout)
    document["schema_version"] = 1
    rewrite_definition(layout, document)

    with pytest.raises(ValidationError, match="schema_version"):
        load_evaluation_suite(layout, skill_id)


def test_a_current_definition_must_carry_a_manifest(layout: Layout) -> None:
    skill_id = subject(layout).id
    author(layout, skill_id, CORPUS_A)
    document = definition_document(layout)
    document.pop("cases")
    rewrite_definition(layout, document)

    with pytest.raises(ValidationError, match="cases"):
        load_evaluation_suite(layout, skill_id)


def test_upgrading_a_legacy_definition_changes_nothing_observable(layout: Layout) -> None:
    """The migration must be invisible to every consumer of evaluation identity.

    A v1 suite and the v2 suite naming exactly what v1 globbed are the same
    evaluation input. If the upgrade moved either digest, every existing piece
    of passing evidence would stop describing the inputs on disk -- the upgrade
    would silently demote every evaluated skill in the repository.
    """
    from skillkernel.evaluation.suite import upgrade_definition_to_manifest

    store = SkillStore(layout)
    skill_id = subject(layout).id
    author(layout, skill_id, CORPUS_A)
    downgrade(layout)

    before_cases = loaded_case_ids(layout, skill_id)
    before_inputs = read_evaluation_inputs(layout, skill_id)
    before_input_digest = suite_module.input_digest(before_inputs)
    before_corpus_digest = corpus_content_digest(before_inputs)

    report = evaluate_skill(layout, skill_id, project=PROJECT)
    assert report.evidence_id is not None
    assert passing_evaluations(layout, store.get(skill_id))

    assert upgrade_definition_to_manifest(layout, skill_id) is True

    document = definition_document(layout)
    assert document["schema_version"] == 2
    assert sorted(document["cases"]) == [
        "examples/negative/a-neg.yaml",
        "examples/positive/a-one.yaml",
    ]
    after_inputs = read_evaluation_inputs(layout, skill_id)
    assert loaded_case_ids(layout, skill_id) == before_cases
    assert suite_module.input_digest(after_inputs) == before_input_digest
    assert corpus_content_digest(after_inputs) == before_corpus_digest
    assert passing_evaluations(layout, store.get(skill_id)), "evidence must stay current"
    assert findings(layout) == []


def test_upgrading_is_idempotent(layout: Layout) -> None:
    skill_id = subject(layout).id
    author(layout, skill_id, CORPUS_A)

    from skillkernel.evaluation.suite import upgrade_definition_to_manifest

    assert upgrade_definition_to_manifest(layout, skill_id) is False


def test_a_legacy_case_filename_the_grammar_cannot_name_refuses_the_upgrade(
    layout: Layout,
) -> None:
    """Stop rather than silently narrowing the corpus.

    ``validate_case_id`` is a write-side rule, so a pre-grammar workspace can
    hold a direct case file whose name the manifest cannot express. Dropping it
    would quietly change what the suite measures, so the upgrade refuses and
    says which file to rename. Nothing is written.
    """
    from skillkernel.evaluation.suite import upgrade_definition_to_manifest

    skill_id = subject(layout).id
    author(layout, skill_id, CORPUS_A)
    downgrade(layout)
    legacy = examples(layout) / "positive" / "Legacy Case.yaml"
    legacy.write_text(yaml.safe_dump(case_document("Legacy Case")), encoding="utf-8")

    with pytest.raises(ValidationError, match=re.escape("Legacy Case.yaml")):
        upgrade_definition_to_manifest(layout, skill_id)
    assert definition_document(layout)["schema_version"] == 1

    with pytest.raises(ValidationError, match=re.escape("Legacy Case.yaml")):
        author(layout, skill_id, CORPUS_B)
    assert definition_document(layout)["corpus_id"] == "corpus-a"


# --- the definition write is the only visibility transition -----------------
#
# Windows in the new order. A reader sees exactly A until the definition
# naming B lands, and exactly B from that instant on. Never A union B, and
# never B under A's metadata.


def assert_sees(layout: Layout, skill_id: str, corpus_id: str, cases: list[str]) -> None:
    loaded = load_evaluation_suite(layout, skill_id)
    assert loaded.corpus_id == corpus_id
    assert sorted(case.case_id for case in loaded.cases) == cases


def test_w0_nothing_written_leaves_a(layout: Layout, monkeypatch: pytest.MonkeyPatch) -> None:
    skill_id = subject(layout).id
    author(layout, skill_id, CORPUS_A)

    seen = intercept(monkeypatch, layout, before="examples/positive/b-one.yaml")
    with pytest.raises(Interrupted):
        author(layout, skill_id, CORPUS_B)

    monkeypatch.undo()
    assert seen == ["skills/core/subject/examples/positive/b-one.yaml"]
    assert files_on_disk(layout) == A_CASES, "nothing landed"
    assert_sees(layout, skill_id, "corpus-a", A_CASES)


def test_w1_after_the_legacy_upgrade_leaves_a(
    layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The upgrade commits on its own, before any of B exists."""
    skill_id = subject(layout).id
    author(layout, skill_id, CORPUS_A)
    downgrade(layout)

    seen = intercept(monkeypatch, layout, after="scorer/eval.yaml")
    with pytest.raises(Interrupted):
        author(layout, skill_id, CORPUS_B)

    monkeypatch.undo()
    assert seen == ["skills/core/subject/scorer/eval.yaml"], "the upgrade writes first"
    assert files_on_disk(layout) == A_CASES, "none of B exists yet"
    assert definition_document(layout)["schema_version"] == 2
    assert_sees(layout, skill_id, "corpus-a", A_CASES)


def test_w1_interrupting_the_legacy_upgrade_itself_leaves_a(
    layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill_id = subject(layout).id
    author(layout, skill_id, CORPUS_A)
    downgrade(layout)

    intercept(monkeypatch, layout, before="scorer/eval.yaml")
    with pytest.raises(Interrupted):
        author(layout, skill_id, CORPUS_B)

    monkeypatch.undo()
    assert definition_document(layout)["schema_version"] == 1
    assert_sees(layout, skill_id, "corpus-a", A_CASES)


def test_w2_a_partially_written_new_corpus_leaves_a(
    layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill_id = subject(layout).id
    author(layout, skill_id, CORPUS_A)

    seen = intercept(monkeypatch, layout, after="examples/positive/b-one.yaml")
    with pytest.raises(Interrupted):
        author(layout, skill_id, CORPUS_B)

    monkeypatch.undo()
    assert seen[-1].endswith("examples/positive/b-one.yaml")
    assert (examples(layout) / "positive" / "b-one.yaml").is_file()
    assert_sees(layout, skill_id, "corpus-a", A_CASES)


def test_w3_every_new_case_written_but_uncommitted_leaves_a(
    layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The decisive window: both corpora are fully on disk at once."""
    skill_id = subject(layout).id
    author(layout, skill_id, CORPUS_A)

    intercept(monkeypatch, layout, before="scorer/eval.yaml")
    with pytest.raises(Interrupted):
        author(layout, skill_id, CORPUS_B)

    monkeypatch.undo()
    assert files_on_disk(layout) == sorted(A_CASES + B_CASES)
    assert_sees(layout, skill_id, "corpus-a", A_CASES)


def test_w4_committed_but_unretired_shows_b(
    layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill_id = subject(layout).id
    author(layout, skill_id, CORPUS_A)

    intercept(monkeypatch, layout, after="scorer/eval.yaml")
    with pytest.raises(Interrupted):
        author(layout, skill_id, CORPUS_B)

    monkeypatch.undo()
    assert files_on_disk(layout) == sorted(A_CASES + B_CASES), "A is still physically present"
    assert_sees(layout, skill_id, "corpus-b", B_CASES)


def test_w5_a_completed_pass_shows_b_and_nothing_else(layout: Layout) -> None:
    skill_id = subject(layout).id
    author(layout, skill_id, CORPUS_A)
    author(layout, skill_id, CORPUS_B)

    assert files_on_disk(layout) == B_CASES
    assert_sees(layout, skill_id, "corpus-b", B_CASES)


@pytest.mark.parametrize("window", ["w0", "w2", "w3", "w4"])
def test_re_running_after_an_interruption_converges(
    layout: Layout, monkeypatch: pytest.MonkeyPatch, window: str
) -> None:
    stops: dict[str, dict[str, str]] = {
        "w0": {"before": "examples/positive/b-one.yaml"},
        "w2": {"after": "examples/positive/b-one.yaml"},
        "w3": {"before": "scorer/eval.yaml"},
        "w4": {"after": "scorer/eval.yaml"},
    }
    skill_id = subject(layout).id
    author(layout, skill_id, CORPUS_A)

    intercept(monkeypatch, layout, **stops[window])
    with pytest.raises(Interrupted):
        author(layout, skill_id, CORPUS_B)
    monkeypatch.undo()

    author(layout, skill_id, CORPUS_B)
    assert files_on_disk(layout) == B_CASES
    assert_sees(layout, skill_id, "corpus-b", B_CASES)
    assert findings(layout) == []


def test_a_case_named_by_both_corpora_is_the_one_window_that_is_not_atomic(
    layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Honest limit: replacement is atomic in *membership*, not in content.

    The manifest decides which files participate, so an interrupted pass never
    changes the case *set* a reader sees. A case the old and the new corpus both
    name is a different matter: it is one path, overwritten in place before the
    commit, so during the window a reader sees A's set carrying B's content for
    that case. Making this atomic too would mean writing every case to a staging
    location and renaming the set, which is a larger change than this slice
    authorizes.
    """
    shared = {
        "corpus_id": "corpus-b",
        "positive": [{"case_id": "a-one", "signals": ["alpha", "extra"]}],
        "negative": [{"case_id": "a-neg", "signals": ["beta"]}],
    }
    skill_id = subject(layout).id
    author(layout, skill_id, CORPUS_A)

    intercept(monkeypatch, layout, before="scorer/eval.yaml")
    with pytest.raises(Interrupted):
        author(layout, skill_id, shared)
    monkeypatch.undo()

    loaded = load_evaluation_suite(layout, skill_id)
    assert loaded.corpus_id == "corpus-a"
    assert sorted(case.case_id for case in loaded.cases) == A_CASES, "the set is still A's"
    changed = next(case for case in loaded.cases if case.case_id == "a-one")
    assert changed.signals == ("alpha", "extra"), "but its content is already B's"


# --- doctor reports unnamed case-shaped content -----------------------------


def test_doctor_reports_case_shaped_content_the_suite_does_not_name(layout: Layout) -> None:
    skill_id = subject(layout).id
    author(layout, skill_id, CORPUS_A)

    nested = examples(layout) / "positive" / "nested"
    nested.mkdir()
    (nested / "x.yaml").write_text(yaml.safe_dump(case_document("x")), encoding="utf-8")
    (examples(layout) / "positive" / "smuggled.yml").write_text(
        yaml.safe_dump(case_document("smuggled")), encoding="utf-8"
    )
    (examples(layout) / "negative" / "planted.yaml").write_text(
        yaml.safe_dump(case_document("planted", "does_not_apply")), encoding="utf-8"
    )

    reported = findings(layout)
    assert all(severity == "WARNING" for severity, _, _ in reported), reported
    assert all(code == "evaluation-cases:unmanifested" for _, code, _ in reported), reported
    assert sorted(location for _, _, location in reported) == [
        "skills/core/subject/examples/negative/planted.yaml",
        "skills/core/subject/examples/positive/nested/x.yaml",
        "skills/core/subject/examples/positive/smuggled.yml",
    ]


def test_doctor_is_silent_about_non_case_authoring_files(layout: Layout) -> None:
    """Not a generic filesystem linter: only case-shaped content is reported."""
    skill_id = subject(layout).id
    author(layout, skill_id, CORPUS_A)

    (examples(layout) / "positive" / "NOTES.md").write_text("working notes", encoding="utf-8")
    (examples(layout) / "README.txt").write_text("how these are organized", encoding="utf-8")
    (examples(layout) / "drafts").mkdir()
    (examples(layout) / "drafts" / "sketch.md").write_text("later", encoding="utf-8")

    assert findings(layout) == []


def test_doctor_reports_an_unnamed_case_at_warning_even_for_a_trusted_skill(
    layout: Layout,
) -> None:
    """Severity is fixed: an unread file cannot invalidate evidence."""
    store = SkillStore(layout)
    skill_id = subject(layout).id
    author(layout, skill_id, CORPUS_A)
    evaluate_skill(layout, skill_id, project=PROJECT)

    (examples(layout) / "positive" / "planted.yaml").write_text(
        yaml.safe_dump(case_document("planted")), encoding="utf-8"
    )

    reported = findings(layout)
    assert [(severity, code) for severity, code, _ in reported] == [
        ("WARNING", "evaluation-cases:unmanifested")
    ]
    assert passing_evaluations(layout, store.get(skill_id)), "evidence is unaffected"


def test_doctor_is_silent_when_the_suite_cannot_be_read(layout: Layout) -> None:
    """Readability is ``evaluation-input``'s finding; this check owns membership.

    With no readable definition there is no manifest, so there is no answer to
    "which files should be here" -- reporting every file as unnamed would be
    guessing.
    """
    skill_id = subject(layout).id
    author(layout, skill_id, CORPUS_A)
    definition_file(layout).unlink()
    (examples(layout) / "positive" / "planted.yaml").write_text(
        yaml.safe_dump(case_document("planted")), encoding="utf-8"
    )

    assert findings(layout) == []


def test_doctor_reports_a_case_shaped_alias_of_a_named_case(layout: Layout) -> None:
    """A symlink is its own entry, not the file it points at."""
    skill_id = subject(layout).id
    author(layout, skill_id, CORPUS_A)
    (examples(layout) / "positive" / "alias.yaml").symlink_to(
        examples(layout) / "positive" / "a-one.yaml"
    )

    assert findings(layout) == [
        (
            "WARNING",
            "evaluation-cases:unmanifested",
            "skills/core/subject/examples/positive/alias.yaml",
        )
    ]


def test_a_clean_workspace_stays_clean(layout: Layout) -> None:
    skill_id = subject(layout).id
    author(layout, skill_id, CORPUS_A)
    evaluate_skill(layout, skill_id, project=PROJECT)

    assert findings(layout) == []
