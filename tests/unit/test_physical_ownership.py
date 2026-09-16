"""Invariant A: a create operation never adopts physical state the index does not own.

The defect this pins down was measured at ``cec6576``. ``SkillStore.create``
called ``mkdir(exist_ok=True)`` and wrote, while its only collision check
(``find_by_slug``) resolves through the index and therefore could not see the
directory it was about to write into. An unindexed ``skills/core/<slug>/`` had
its ``skill.yaml`` and ``history.yaml`` destroyed, its ``examples/`` kept, and
``doctor`` reported nothing before or after.

The kept examples are the part that matters. ``load_evaluation_suite`` globs the
examples directory, so a newly created skill's suite loaded four cases where two
had been authored -- two of them inherited from the skill that had just been
destroyed. A skill must never inherit evidence it did not earn, so this is a
provenance failure rather than a housekeeping one.

The bundle installer already refused the identical topology. Two write paths into
the same tree disagreeing is the real defect; the repair is one shared authority,
not a second policy.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
from skillkernel.core.errors import SkillKernelError, UnsafeOperationError, ValidationError
from skillkernel.core.paths import Layout
from skillkernel.evaluation.suite import load_evaluation_suite, write_evaluation_suite
from skillkernel.registry import Registry
from skillkernel.skills.store import SkillStore
from skillkernel.validation.doctor import run_doctor

ACTIVATION = {"require_any": ["trigger"], "require_all": [], "exclude_any": []}

SENTINEL_SKILL = "SENTINEL: a prior skill.yaml that must survive\n"
SENTINEL_HISTORY = "SENTINEL: a prior history.yaml that must survive\n"


def tree(root: Path) -> dict[str, str]:
    """Every file's digest plus every directory, so a stray mkdir is visible too."""
    snapshot: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        key = path.relative_to(root).as_posix()
        snapshot[key] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "<dir>"
    assert snapshot, f"{root} is empty; a comparison would be vacuous"
    return snapshot


def plant(
    layout: Layout,
    *,
    scope: str = "core",
    slug: str = "sample-skill",
    skill: bool = True,
    history: bool = True,
    examples: bool = False,
) -> Path:
    """Physical skill state that no index entry owns."""
    directory = layout.skills_dir / scope / slug
    directory.mkdir(parents=True)
    if skill:
        (directory / "skill.yaml").write_text(SENTINEL_SKILL, encoding="utf-8")
    if history:
        (directory / "history.yaml").write_text(SENTINEL_HISTORY, encoding="utf-8")
    if examples:
        for polarity, signal in (("positive", "ghost"), ("negative", "ghost")):
            folder = directory / "examples" / polarity
            folder.mkdir(parents=True)
            (folder / f"ghost-{polarity}.yaml").write_text(
                "schema_version: 1\n"
                f"case_id: ghost-{polarity}\n"
                f"expected: {'applies' if polarity == 'positive' else 'does_not_apply'}\n"
                f"signals: [{signal}]\n"
                "description: from the destroyed skill\n",
                encoding="utf-8",
            )
    return directory


def create(skills: SkillStore, slug: str = "sample-skill", scope: str = "core") -> Any:
    return skills.create(
        name=slug.replace("-", " ").title(),
        scope=scope,
        slug=slug,
        purpose="A purpose.",
        applies_when=["when triggered"],
        do_not_apply_when=["when forbidden"],
        activation_rules=ACTIVATION,
    )


# --- the refusal ------------------------------------------------------------


@pytest.mark.parametrize("scope", ["core", "project", "discovered"])
def test_create_refuses_a_destination_that_exists_but_no_skill_owns(
    layout: Layout, skills: SkillStore, scope: str
) -> None:
    plant(layout, scope=scope)
    with pytest.raises(UnsafeOperationError, match="unmanaged directory"):
        create(skills, scope=scope)


def test_the_interrupted_residue_is_refused_too(layout: Layout, skills: SkillStore) -> None:
    """The state an interruption actually leaves: history written, index not yet."""
    plant(layout, skill=False, history=True)
    with pytest.raises(UnsafeOperationError, match="unmanaged directory"):
        create(skills)


def test_a_destination_holding_only_a_record_is_refused(layout: Layout, skills: SkillStore) -> None:
    plant(layout, skill=True, history=False)
    with pytest.raises(UnsafeOperationError, match="unmanaged directory"):
        create(skills)


# --- what the refusal must guarantee ----------------------------------------


def test_the_refused_create_leaves_the_workspace_byte_identical(
    layout: Layout, skills: SkillStore
) -> None:
    directory = plant(layout, examples=True)
    before = tree(layout.root)
    with pytest.raises(UnsafeOperationError):
        create(skills)
    assert tree(layout.root) == before
    assert (directory / "skill.yaml").read_text(encoding="utf-8") == SENTINEL_SKILL
    assert (directory / "history.yaml").read_text(encoding="utf-8") == SENTINEL_HISTORY


def test_the_refused_create_burns_no_identifier(layout: Layout, skills: SkillStore) -> None:
    plant(layout)
    before = skills.registry.load_index().next_sequence
    with pytest.raises(UnsafeOperationError):
        create(skills)
    assert skills.registry.load_index().next_sequence == before


def test_the_refused_create_registers_nothing(layout: Layout, skills: SkillStore) -> None:
    plant(layout)
    with pytest.raises(UnsafeOperationError):
        create(skills)
    assert skills.ids() == []


def test_the_refusal_precedes_allocation_and_every_write(
    layout: Layout, skills: SkillStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ordering asserted behaviourally, not by reading the source.

    Every mutation the create path can perform is replaced with a detonator. The
    refusal is only proven to come first if none of them fires.
    """
    plant(layout)

    def detonate(*args: object, **kwargs: object) -> None:
        raise AssertionError("a mutation ran before the destination was refused")

    monkeypatch.setattr(Registry, "allocate_id", detonate)
    monkeypatch.setattr("skillkernel.skills.store.write_history", detonate)
    monkeypatch.setattr("skillkernel.registry.index.write_yaml_file", detonate)

    with pytest.raises(UnsafeOperationError, match="unmanaged directory"):
        create(skills)


def test_a_broken_symlink_at_the_destination_is_refused_before_allocation(
    layout: Layout, skills: SkillStore
) -> None:
    """``exists()`` follows symlinks, so a broken one reads as absent.

    Found in the red-team pass. The destination slipped past the ownership guard
    and was refused later by repository containment -- after an identifier had
    been burned and the index rewritten, turning a refusal into a mutation.
    """
    (layout.skills_dir / "core").mkdir(parents=True, exist_ok=True)
    (layout.skills_dir / "core" / "sample-skill").symlink_to("/nonexistent/nowhere")

    before = tree(layout.root)
    sequence = skills.registry.load_index().next_sequence
    with pytest.raises(UnsafeOperationError, match="unmanaged directory"):
        create(skills)
    assert tree(layout.root) == before
    assert skills.registry.load_index().next_sequence == sequence


def test_a_symlinked_destination_is_refused(
    layout: Layout, skills: SkillStore, tmp_path: Path
) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (layout.skills_dir / "core").mkdir(parents=True, exist_ok=True)
    (layout.skills_dir / "core" / "sample-skill").symlink_to(elsewhere)
    with pytest.raises(UnsafeOperationError, match="unmanaged directory"):
        create(skills)


def test_a_plain_file_at_the_destination_is_refused(layout: Layout, skills: SkillStore) -> None:
    (layout.skills_dir / "core").mkdir(parents=True, exist_ok=True)
    (layout.skills_dir / "core" / "sample-skill").write_text("not a directory", encoding="utf-8")
    with pytest.raises(UnsafeOperationError, match="unmanaged directory"):
        create(skills)


def test_an_empty_destination_directory_is_refused(layout: Layout, skills: SkillStore) -> None:
    """Empty is not the same as absent: something made it, and nothing owns it."""
    (layout.skills_dir / "core" / "sample-skill").mkdir(parents=True)
    with pytest.raises(UnsafeOperationError, match="unmanaged directory"):
        create(skills)


# --- the provenance consequence ---------------------------------------------


def test_foreign_evaluation_cases_are_never_adopted(layout: Layout, skills: SkillStore) -> None:
    """The measured contamination: a suite that loaded four cases from two authored."""
    plant(layout, examples=True)
    with pytest.raises(UnsafeOperationError):
        create(skills)
    assert skills.ids() == []

    # The foreign cases are still on disk, still owned by nobody, and now cannot
    # become any skill's evidence because the create that would have adopted them
    # refused.
    ghost = layout.skills_dir / "core" / "sample-skill" / "examples" / "positive"
    assert (ghost / "ghost-positive.yaml").is_file()


def test_a_skill_created_elsewhere_carries_only_its_own_cases(
    layout: Layout, skills: SkillStore
) -> None:
    plant(layout, slug="abandoned", examples=True)
    record = create(skills, slug="honest")
    write_evaluation_suite(
        layout,
        record.id,
        corpus_id="honest-corpus",
        pass_threshold=0.9,
        max_false_activation_rate=0.1,
        positive=[{"case_id": "real-yes", "signals": ["trigger"]}],
        negative=[{"case_id": "real-no", "signals": ["nope"]}],
    )
    suite = load_evaluation_suite(layout, record.id)
    assert sorted(case.case_id for case in suite.cases) == ["real-no", "real-yes"]


# --- what must keep working -------------------------------------------------


def test_creation_still_succeeds_when_the_destination_is_absent(skills: SkillStore) -> None:
    record = create(skills)
    assert record.slug == "sample-skill"
    assert skills.ids() == [record.id]


def test_an_orphan_does_not_block_a_different_slug(layout: Layout, skills: SkillStore) -> None:
    plant(layout, slug="abandoned")
    record = create(skills, slug="unrelated")
    assert skills.ids() == [record.id]


def test_an_orphan_in_one_scope_does_not_block_another(layout: Layout, skills: SkillStore) -> None:
    plant(layout, scope="core", slug="shared-name")
    record = create(skills, scope="project", slug="shared-name")
    assert record.scope == "project"


def test_an_indexed_duplicate_still_reports_the_duplicate(skills: SkillStore) -> None:
    """The index-owned collision keeps its own diagnostic; this is not that case."""
    create(skills)
    with pytest.raises(ValidationError, match="already exists"):
        create(skills)


def test_the_installer_still_refuses_the_same_topology(layout: Layout, make_bundle: Any) -> None:
    from skillkernel.bundles.catalog import load_bundle
    from skillkernel.bundles.installer import install_bundle

    plant(layout, slug="demo-skill")
    bundle = load_bundle("demo-skill", root=make_bundle())
    before = tree(layout.root)
    with pytest.raises(UnsafeOperationError, match="unmanaged directory"):
        install_bundle(layout, bundle)
    assert tree(layout.root) == before


def test_doctor_still_runs_cleanly_over_a_workspace_with_no_orphan(
    skills: SkillStore, layout: Layout
) -> None:
    create(skills)
    report = run_doctor(layout)
    assert report.is_complete
    assert not report.has_errors


def test_the_refusal_is_an_ordinary_domain_error(layout: Layout, skills: SkillStore) -> None:
    """Not an internal fault: doctor and the CLI must treat it as a domain outcome."""
    plant(layout)
    with pytest.raises(SkillKernelError):
        create(skills)


# --- the same invariant at the allocation boundary --------------------------
#
# VS5 closed this for skills, whose destination is derived from a caller-chosen
# slug. Four other stores derive it from the identifier ``allocate_id`` is about
# to issue, and none of them looked before writing.
#
# An interruption cannot produce the collision: ``allocate_id`` persists
# ``next_sequence`` first, so an interrupted create orphans a record at an
# identifier that is never reissued. It is reached by the index moving *backwards*
# relative to the records tree -- ``git checkout <older> -- <domain>/registry/
# index.yaml``, a partial revert, a restored backup, or a records tree copied from
# another workspace. Both files are tracked, and this project keeps the repository
# as its system of record, so a partial checkout is an ordinary operation rather
# than a contrived one. ``rewind`` below restores an earlier index byte for byte,
# which is exactly what that checkout does.

from skillkernel.discovery.observations import ObservationStore  # noqa: E402
from skillkernel.evidence.ledger import EvidenceLedger  # noqa: E402
from skillkernel.experiments.store import ExperimentStore  # noqa: E402
from skillkernel.knowledge.store import KnowledgeStore  # noqa: E402


def rewind(registry: Registry, saved: bytes) -> None:
    """Restore an earlier index, as ``git checkout <older> -- <index>`` does."""
    registry.index_file.write_bytes(saved)


def add_knowledge(store: KnowledgeStore, statement: str) -> Any:
    return store.add(statement=statement, scope="project", source_type="research")


def add_experiment(layout: Layout, title: str) -> Any:
    return ExperimentStore(layout).add(
        title=title,
        hypothesis="h",
        independent_variable="v",
        control={"label": "c", "description": "c"},
        treatment={"label": "t", "description": "t"},
        corpus={"id": "corpus", "description": "d", "cases": ["c1"]},
        scorer={"name": "s", "version": "1", "deterministic": True},
        primary_metric={"name": "m", "direction": "maximize", "description": None},
        pass_threshold=0.9,
        failure_threshold=0.5,
        project="fixture-project",
    )


# --- knowledge --------------------------------------------------------------


def test_knowledge_refuses_a_destination_a_rewound_index_would_reclaim(layout: Layout) -> None:
    store = KnowledgeStore(layout)
    saved = store.registry.index_file.read_bytes()
    record = add_knowledge(store, "The original claim, which must survive.")
    path = store.registry.path_of(record.id)
    original = path.read_bytes()

    rewind(store.registry, saved)
    reloaded = KnowledgeStore(layout)
    assert reloaded.ids() == [], "the rewind must actually orphan the record"

    before = tree(layout.root)
    sequence = reloaded.registry.load_index().next_sequence
    with pytest.raises(UnsafeOperationError, match="unmanaged"):
        add_knowledge(reloaded, "A replacement claim that must never land.")

    assert path.read_bytes() == original, "the orphaned record was overwritten"
    assert tree(layout.root) == before
    assert reloaded.registry.load_index().next_sequence == sequence
    assert KnowledgeStore(layout).ids() == []


def test_the_refused_knowledge_create_precedes_every_write(
    layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = KnowledgeStore(layout)
    saved = store.registry.index_file.read_bytes()
    add_knowledge(store, "The original claim, which must survive.")
    rewind(store.registry, saved)

    def detonate(*args: object, **kwargs: object) -> None:
        raise AssertionError("a write ran before the destination was refused")

    monkeypatch.setattr("skillkernel.registry.index.write_yaml_file", detonate)
    with pytest.raises(UnsafeOperationError, match="unmanaged"):
        add_knowledge(KnowledgeStore(layout), "A replacement claim.")


def test_ordinary_knowledge_creation_is_unchanged(layout: Layout) -> None:
    store = KnowledgeStore(layout)
    first = add_knowledge(store, "A testable claim about the world.")
    second = add_knowledge(store, "A second, unrelated testable claim.")
    assert [first.id, second.id] == ["K-0001", "K-0002"]
    assert store.ids() == ["K-0001", "K-0002"]


def test_the_orphaned_knowledge_record_is_still_reported(layout: Layout) -> None:
    """Invariant B's reporting must survive Invariant A's refusal."""
    store = KnowledgeStore(layout)
    saved = store.registry.index_file.read_bytes()
    record = add_knowledge(store, "A claim that becomes an orphan.")
    rewind(store.registry, saved)

    report = run_doctor(layout)
    assert report.is_complete
    assert not report.has_errors
    assert [(f.severity, f.location) for f in report.warnings] == [
        ("WARNING", f"knowledge/records/{record.id}.yaml")
    ]


# --- evidence and observations, the same shape ------------------------------


def test_evidence_refuses_a_reclaimed_destination(layout: Layout) -> None:
    ledger = EvidenceLedger(layout)
    saved = ledger.registry.index_file.read_bytes()
    record = ledger.record(
        kind="observation_note",
        summary="The original measurement.",
        project="fixture-project",
        source_type="tool",
        source_detail="pytest",
    )
    path = ledger.registry.path_of(record.id)
    original = path.read_bytes()
    rewind(ledger.registry, saved)

    before = tree(layout.root)
    with pytest.raises(UnsafeOperationError, match="unmanaged"):
        EvidenceLedger(layout).record(
            kind="observation_note",
            summary="A replacement that must never land.",
            project="fixture-project",
            source_type="tool",
            source_detail="pytest",
        )
    assert path.read_bytes() == original
    assert tree(layout.root) == before


def test_observations_refuse_a_reclaimed_destination(layout: Layout) -> None:
    store = ObservationStore(layout)
    saved = store.registry.index_file.read_bytes()
    record = store.add(
        category="pattern",
        task="The original task.",
        context="ctx",
        classification="success",
        outcome="resolved",
        project="fixture-project",
    )
    path = store.registry.path_of(record.id)
    original = path.read_bytes()
    rewind(store.registry, saved)

    with pytest.raises(UnsafeOperationError, match="unmanaged"):
        ObservationStore(layout).add(
            category="pattern",
            task="A replacement that must never land.",
            context="ctx",
            classification="success",
            outcome="resolved",
            project="fixture-project",
        )
    assert path.read_bytes() == original


# --- experiments: a new identity, versus a version of an owned one ----------


def test_a_new_experiment_refuses_an_unowned_destination(layout: Layout) -> None:
    store = ExperimentStore(layout)
    saved = store.registry.index_file.read_bytes()
    definition = add_experiment(layout, "ORIGINAL EXPERIMENT")
    path = store.definition_path(definition.id, 1)
    original = path.read_bytes()
    rewind(store.registry, saved)
    assert ExperimentStore(layout).ids() == []

    before = tree(layout.root)
    sequence = ExperimentStore(layout).registry.load_index().next_sequence
    with pytest.raises(UnsafeOperationError, match="unmanaged"):
        add_experiment(layout, "REPLACEMENT EXPERIMENT")

    assert path.read_bytes() == original, "the orphaned definition was overwritten"
    assert tree(layout.root) == before
    assert ExperimentStore(layout).registry.load_index().next_sequence == sequence


def test_revising_an_owned_experiment_still_works(layout: Layout) -> None:
    """Case 2, which must not be caught by case 1's refusal.

    ``revise`` reuses the existing identifier and never allocates, so the new
    guard cannot see it -- but the whole point is that a legitimate second
    version lands inside a directory that already exists.
    """
    store = ExperimentStore(layout)
    definition = add_experiment(layout, "An experiment worth revising")
    store.freeze(definition.id)
    revised = store.revise(definition.id, changes={"pass_threshold": 0.95}, reason="raise the bar")

    assert revised.version == 2
    assert store.versions(definition.id) == [1, 2]
    assert store.definition_path(definition.id, 1).is_file()
    assert store.get(definition.id).version == 2


def test_freezing_an_owned_experiment_still_works(layout: Layout) -> None:
    store = ExperimentStore(layout)
    definition = add_experiment(layout, "An experiment worth freezing")
    frozen = store.freeze(definition.id)
    assert frozen.frozen
    assert store.versions(definition.id) == [1]


def test_a_retained_first_version_is_not_an_ownership_collision(layout: Layout) -> None:
    """The regression the contract names explicitly."""
    store = ExperimentStore(layout)
    definition = add_experiment(layout, "An experiment with history")
    store.freeze(definition.id)
    store.revise(definition.id, changes={"pass_threshold": 0.95}, reason="first revision")
    store.freeze(definition.id)
    store.revise(definition.id, changes={"failure_threshold": 0.4}, reason="second revision")

    assert store.versions(definition.id) == [1, 2, 3]
    report = run_doctor(layout)
    assert report.is_complete
    assert not report.has_errors
    assert report.warnings == []


def test_a_second_new_experiment_is_unaffected_by_the_first(layout: Layout) -> None:
    first = add_experiment(layout, "First")
    second = add_experiment(layout, "Second")
    assert [first.id, second.id] == ["EXP-0001", "EXP-0002"]
