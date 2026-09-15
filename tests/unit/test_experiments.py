"""Experiment immutability, verdict derivation and threshold boundaries.

The invariants under test, one test each at minimum:

  1. a definition is frozen before any result may be recorded;
  2. freezing gives the definition a stable fingerprint;
  3. post-freeze modification invalidates result compatibility;
  4. the caller supplies measurements, never the verdict;
  5. the kernel derives the verdict from the frozen rules;
  6. a revision produces a new version;
  7. the historical definition file remains intact.
"""

from __future__ import annotations

from typing import Any

import pytest
from skillkernel.core.errors import IntegrityError, ValidationError
from skillkernel.core.yamlio import load_yaml_file, write_yaml_file
from skillkernel.experiments.model import evaluate_verdict, find_guardrail_violations
from skillkernel.experiments.store import ExperimentStore

START = "2024-01-31T11:00:00Z"


def make(store: ExperimentStore, **overrides: Any) -> Any:
    kwargs: dict[str, Any] = {
        "title": "Does batching help?",
        "hypothesis": "Batching reduces wall-clock time without raising error rate.",
        "independent_variable": "batch size",
        "control": {"label": "unbatched", "description": "one item per call"},
        "treatment": {"label": "batched", "description": "sixteen items per call"},
        "corpus": {"id": "corpus-a", "description": "40 recorded tasks", "cases": ["c1", "c2"]},
        "scorer": {"name": "wallclock", "version": "1", "deterministic": True},
        "primary_metric": {"name": "speedup", "direction": "maximize", "description": None},
        "pass_threshold": 1.5,
        "failure_threshold": 1.0,
        "project": "demo",
    }
    kwargs.update(overrides)
    return store.add(**kwargs)


def record(store: ExperimentStore, experiment_id: str, value: float, **kwargs: Any) -> Any:
    return store.record_result(
        experiment_id,
        primary_metric_value=value,
        control_metrics={"seconds": 10.0},
        treatment_metrics={"seconds": 5.0},
        interpretation="measured",
        started_at=START,
        **kwargs,
    )


# --- invariant 1: freeze before results ------------------------------------


def test_a_new_definition_is_not_frozen(experiments: ExperimentStore) -> None:
    definition = make(experiments)
    assert definition.frozen is False
    assert definition.stored_hash is None


def test_recording_a_result_before_freezing_is_refused(experiments: ExperimentStore) -> None:
    definition = make(experiments)
    with pytest.raises(ValidationError, match="is not frozen"):
        record(experiments, definition.id, 2.0)


def test_no_result_file_is_written_when_recording_is_refused(
    experiments: ExperimentStore,
) -> None:
    definition = make(experiments)
    with pytest.raises(ValidationError):
        record(experiments, definition.id, 2.0)
    assert experiments.results(definition.id) == []


# --- invariant 2: freezing fixes a fingerprint -----------------------------


def test_freezing_records_a_hash_and_a_timestamp(experiments: ExperimentStore) -> None:
    definition = experiments.freeze(make(experiments).id)
    assert definition.frozen is True
    assert definition.frozen_at is not None
    assert definition.stored_hash is not None
    assert definition.hash_matches()


def test_freezing_is_idempotent(experiments: ExperimentStore) -> None:
    definition = make(experiments)
    first = experiments.freeze(definition.id)
    second = experiments.freeze(definition.id)
    assert first.stored_hash == second.stored_hash
    assert first.frozen_at == second.frozen_at


def test_the_fingerprint_covers_the_thresholds(experiments: ExperimentStore) -> None:
    definition = experiments.freeze(make(experiments).id)
    document = dict(definition.raw)
    before = definition.current_hash()
    document["pass_threshold"] = 99.0
    from skillkernel.experiments.model import definition_hash

    assert definition_hash(document) != before


def test_the_fingerprint_ignores_the_updated_at_bookkeeping_field(
    experiments: ExperimentStore,
) -> None:
    definition = experiments.freeze(make(experiments).id)
    from skillkernel.experiments.model import definition_hash

    document = dict(definition.raw)
    document["updated_at"] = "2030-01-01T00:00:00Z"
    assert definition_hash(document) == definition.current_hash()


# --- invariant 3: post-freeze edits invalidate -----------------------------


def test_editing_a_frozen_definition_blocks_further_results(
    experiments: ExperimentStore,
) -> None:
    definition = experiments.freeze(make(experiments).id)
    path = experiments.definition_path(definition.id, definition.version)
    document = load_yaml_file(path)
    # A coherent but self-serving gate change: lower the bar so a mediocre
    # measurement would read as a pass.
    document["pass_threshold"] = 1.1
    write_yaml_file(path, document)

    with pytest.raises(IntegrityError, match="edited after it was frozen"):
        record(experiments, definition.id, 1.2)


def test_editing_a_frozen_definition_is_reported_by_verify(
    experiments: ExperimentStore,
) -> None:
    definition = experiments.freeze(make(experiments).id)
    path = experiments.definition_path(definition.id, definition.version)
    document = load_yaml_file(path)
    document["hypothesis"] = "a different hypothesis entirely"
    write_yaml_file(path, document)

    report = "\n".join(str(finding) for finding in experiments.verify())
    assert "edited after freezing" in report


def test_refreezing_an_edited_definition_is_refused(experiments: ExperimentStore) -> None:
    definition = experiments.freeze(make(experiments).id)
    path = experiments.definition_path(definition.id, definition.version)
    document = load_yaml_file(path)
    document["pass_threshold"] = 1.1
    write_yaml_file(path, document)

    with pytest.raises(IntegrityError, match="no longer matches"):
        experiments.freeze(definition.id)


def test_an_untouched_store_verifies_clean(experiments: ExperimentStore) -> None:
    definition = experiments.freeze(make(experiments).id)
    record(experiments, definition.id, 2.0)
    assert experiments.verify() == []


# --- invariants 4 and 5: the kernel derives the verdict --------------------


def test_record_result_accepts_no_verdict_from_the_caller(
    experiments: ExperimentStore,
) -> None:
    """The contract is structural: there is no parameter through which to pass one."""
    import inspect

    parameters = inspect.signature(ExperimentStore.record_result).parameters
    assert "verdict" not in parameters


def test_the_verdict_is_derived_from_the_frozen_thresholds(
    experiments: ExperimentStore,
) -> None:
    definition = experiments.freeze(make(experiments).id)
    assert record(experiments, definition.id, 2.0).verdict == "pass"
    assert record(experiments, definition.id, 0.9).verdict == "fail"
    assert record(experiments, definition.id, 1.2).verdict == "inconclusive"


@pytest.mark.parametrize(
    ("value", "expected"),
    [(1.49, "inconclusive"), (1.5, "pass"), (1.51, "pass"), (1.0, "fail"), (1.01, "inconclusive")],
)
def test_maximized_metric_boundaries_are_inclusive(
    experiments: ExperimentStore, value: float, expected: str
) -> None:
    definition = experiments.freeze(make(experiments).id)
    assert record(experiments, definition.id, value).verdict == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0.09, "pass"), (0.1, "pass"), (0.11, "inconclusive"), (0.5, "fail"), (0.49, "inconclusive")],
)
def test_minimized_metric_boundaries_are_inclusive(
    experiments: ExperimentStore, value: float, expected: str
) -> None:
    definition = experiments.freeze(
        make(
            experiments,
            primary_metric={"name": "error_rate", "direction": "minimize", "description": None},
            pass_threshold=0.1,
            failure_threshold=0.5,
        ).id
    )
    assert record(experiments, definition.id, value).verdict == expected


def test_a_guardrail_violation_forces_failure_despite_a_passing_headline(
    experiments: ExperimentStore,
) -> None:
    definition = experiments.freeze(
        make(
            experiments,
            guardrail_metrics=[
                {
                    "name": "error_rate",
                    "direction": "minimize",
                    "threshold": 0.05,
                    "description": None,
                }
            ],
        ).id
    )
    result = record(experiments, definition.id, 3.0, guardrail_values={"error_rate": 0.4})
    assert result.verdict == "fail"
    assert result.guardrail_violations == ("error_rate",)


def test_an_unmeasured_guardrail_counts_as_a_violation(experiments: ExperimentStore) -> None:
    definition = experiments.freeze(
        make(
            experiments,
            guardrail_metrics=[
                {
                    "name": "error_rate",
                    "direction": "minimize",
                    "threshold": 0.05,
                    "description": None,
                }
            ],
        ).id
    )
    result = record(experiments, definition.id, 3.0)
    assert result.verdict == "fail"
    assert "error_rate (not measured)" in result.guardrail_violations


def test_a_satisfied_guardrail_does_not_block_a_pass(experiments: ExperimentStore) -> None:
    definition = experiments.freeze(
        make(
            experiments,
            guardrail_metrics=[
                {
                    "name": "error_rate",
                    "direction": "minimize",
                    "threshold": 0.05,
                    "description": None,
                }
            ],
        ).id
    )
    result = record(experiments, definition.id, 3.0, guardrail_values={"error_rate": 0.01})
    assert result.verdict == "pass"


@pytest.mark.parametrize(
    ("direction", "threshold", "measured", "breached"),
    [
        ("minimize", 0.05, 0.05, False),
        ("minimize", 0.05, 0.051, True),
        ("maximize", 0.95, 0.95, False),
        ("maximize", 0.95, 0.949, True),
    ],
)
def test_guardrail_boundaries_are_inclusive(
    direction: str, threshold: float, measured: float, breached: bool
) -> None:
    guardrails = [{"name": "g", "direction": direction, "threshold": threshold}]
    violations = find_guardrail_violations(guardrails, {"g": measured})
    assert bool(violations) is breached


def test_evaluate_verdict_is_a_pure_function_of_its_inputs() -> None:
    assert (
        evaluate_verdict(
            direction="maximize",
            value=2.0,
            pass_threshold=1.5,
            failure_threshold=1.0,
            guardrail_violations=[],
        )
        == "pass"
    )
    assert (
        evaluate_verdict(
            direction="maximize",
            value=2.0,
            pass_threshold=1.5,
            failure_threshold=1.0,
            guardrail_violations=["g"],
        )
        == "fail"
    )


# --- threshold coherence ---------------------------------------------------


def test_incoherent_thresholds_are_rejected_for_a_maximized_metric(
    experiments: ExperimentStore,
) -> None:
    with pytest.raises(ValidationError, match="failure_threshold"):
        make(experiments, pass_threshold=1.0, failure_threshold=2.0)


def test_incoherent_thresholds_are_rejected_for_a_minimized_metric(
    experiments: ExperimentStore,
) -> None:
    with pytest.raises(ValidationError, match="failure_threshold"):
        make(
            experiments,
            primary_metric={"name": "err", "direction": "minimize", "description": None},
            pass_threshold=0.5,
            failure_threshold=0.1,
        )


# --- invariants 6 and 7: revision --------------------------------------------


def test_a_revision_creates_a_new_unfrozen_version(experiments: ExperimentStore) -> None:
    definition = experiments.freeze(make(experiments).id)
    revised = experiments.revise(
        definition.id, changes={"pass_threshold": 1.2}, reason="1.5 was unreachable in practice"
    )
    assert revised.version == 2
    assert revised.frozen is False
    assert revised.pass_threshold == 1.2
    assert revised.raw["supersedes_version"] == 1


def test_a_revision_leaves_the_previous_version_file_byte_identical(
    experiments: ExperimentStore,
) -> None:
    definition = experiments.freeze(make(experiments).id)
    path = experiments.definition_path(definition.id, 1)
    before = path.read_bytes()

    experiments.revise(definition.id, changes={"pass_threshold": 1.2}, reason="recalibrated")

    assert path.read_bytes() == before
    assert experiments.versions(definition.id) == [1, 2]


def test_results_recorded_against_the_old_version_remain_interpretable(
    experiments: ExperimentStore,
) -> None:
    definition = experiments.freeze(make(experiments).id)
    original = record(experiments, definition.id, 2.0)
    experiments.revise(definition.id, changes={"pass_threshold": 9.0}, reason="raised the bar")

    stored = experiments.results(definition.id)[0]
    assert stored.verdict == original.verdict == "pass"
    assert stored.experiment_version == 1
    assert experiments.verify() == []


def test_a_revision_must_state_a_reason(experiments: ExperimentStore) -> None:
    definition = experiments.freeze(make(experiments).id)
    with pytest.raises(ValidationError, match="must state why"):
        experiments.revise(definition.id, changes={"pass_threshold": 1.2}, reason="   ")


@pytest.mark.parametrize("field", ["id", "version", "created_at", "supersedes_version"])
def test_a_revision_may_not_rewrite_identity_fields(
    experiments: ExperimentStore, field: str
) -> None:
    definition = experiments.freeze(make(experiments).id)
    with pytest.raises(ValidationError, match="may not change"):
        experiments.revise(definition.id, changes={field: "tampered"}, reason="attempt")


def test_a_result_citing_a_nonexistent_version_is_reported(
    experiments: ExperimentStore,
) -> None:
    definition = experiments.freeze(make(experiments).id)
    record(experiments, definition.id, 2.0)
    result_path = experiments.results_dir(definition.id) / "run-0001.yaml"
    document = load_yaml_file(result_path)
    document["experiment_version"] = 7
    write_yaml_file(result_path, document)

    report = "\n".join(str(finding) for finding in experiments.verify())
    assert "has no definition file" in report


# --- result bookkeeping ----------------------------------------------------


def test_runs_are_numbered_sequentially_and_never_overwritten(
    experiments: ExperimentStore,
) -> None:
    definition = experiments.freeze(make(experiments).id)
    first = record(experiments, definition.id, 2.0)
    second = record(experiments, definition.id, 2.1)
    assert first.run_id.endswith("run-0001")
    assert second.run_id.endswith("run-0002")
    assert len(experiments.results(definition.id)) == 2


def test_passing_results_filters_by_verdict(experiments: ExperimentStore) -> None:
    definition = experiments.freeze(make(experiments).id)
    record(experiments, definition.id, 2.0)
    record(experiments, definition.id, 0.5)
    assert [r.verdict for r in experiments.passing_results(definition.id)] == ["pass"]


def test_a_result_carries_the_definition_hash_it_was_measured_against(
    experiments: ExperimentStore,
) -> None:
    definition = experiments.freeze(make(experiments).id)
    assert record(experiments, definition.id, 2.0).definition_hash == definition.stored_hash


def test_results_are_empty_for_an_experiment_that_has_never_run(
    experiments: ExperimentStore,
) -> None:
    assert experiments.results(make(experiments).id) == []


def test_an_incoherent_gate_edit_is_caught_by_schema_coherence_before_integrity(
    experiments: ExperimentStore,
) -> None:
    """Both guards are live, and the coherence check fires first.

    Editing a frozen definition into a state that can never be satisfied
    (pass below failure on a maximized metric) is rejected on load, so the
    definition cannot even be read back to reach the tamper check.
    """
    definition = experiments.freeze(make(experiments).id)
    path = experiments.definition_path(definition.id, definition.version)
    document = load_yaml_file(path)
    document["pass_threshold"] = 0.1
    write_yaml_file(path, document)

    with pytest.raises(ValidationError, match="must be <="):
        experiments.get(definition.id)
