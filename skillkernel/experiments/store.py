"""Experiment storage, freezing, revision and result recording."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from skillkernel.core.clock import now_iso
from skillkernel.core.errors import IntegrityError, ValidationError
from skillkernel.core.ids import EXPERIMENT
from skillkernel.core.paths import Layout
from skillkernel.core.yamlio import load_yaml_file, write_yaml_file
from skillkernel.experiments.model import (
    RESULT_SCHEMA_VERSION,
    ExperimentDefinition,
    ExperimentResult,
    definition_hash,
    evaluate_verdict,
    find_guardrail_violations,
    new_experiment_document,
)
from skillkernel.registry import Registry

__all__ = ["ExperimentStore"]

_DEFINITION_HEADER = (
    "# SkillKernel experiment definition. Once frozen, this file must not change:\n"
    "# its hash is recorded in every result. To change a gate, use 'experiment revise',\n"
    "# which writes a new version alongside this one.\n"
)
_RESULT_HEADER = (
    "# SkillKernel experiment result. The verdict is computed by the kernel from the\n"
    "# frozen definition's thresholds, not supplied by the caller.\n"
)


@dataclass(frozen=True)
class ExperimentFinding:
    experiment_id: str
    message: str

    def __str__(self) -> str:
        return f"{self.experiment_id}: {self.message}"


class ExperimentStore:
    def __init__(self, layout: Layout) -> None:
        self.layout = layout
        self.registry = Registry(
            layout,
            kind="experiments",
            domain_dir=layout.experiments_dir,
            id_prefix=EXPERIMENT,
            records_subdir="definitions",
            # A definition is <EXP-ID>/v<N>.yaml, a directory deep. The flat glob
            # this replaces could only see a shape the store never writes.
            record_finder=layout.experiment_definition_states,
        )

    # --- locations ---------------------------------------------------------
    def definition_dir(self, experiment_id: str) -> Path:
        return self.layout.experiments_dir / "definitions" / experiment_id

    def definition_path(self, experiment_id: str, version: int) -> Path:
        return self.definition_dir(experiment_id) / f"v{version}.yaml"

    def results_dir(self, experiment_id: str) -> Path:
        return self.layout.experiments_dir / "results" / experiment_id

    # --- reads -------------------------------------------------------------
    def ids(self) -> list[str]:
        return self.registry.ids()

    def has(self, experiment_id: str) -> bool:
        return self.registry.has(experiment_id)

    def get(self, experiment_id: str) -> ExperimentDefinition:
        """Load the current (highest registered) version of a definition."""
        document = self.registry.load(experiment_id)
        return ExperimentDefinition.from_document(document, source=experiment_id)

    def get_version(self, experiment_id: str, version: int) -> ExperimentDefinition:
        path = self.definition_path(experiment_id, version)
        if not path.is_file():
            raise ValidationError(f"{experiment_id} has no version {version} at {path}")
        return ExperimentDefinition.from_document(
            load_yaml_file(path), source=f"{experiment_id} v{version}"
        )

    def versions(self, experiment_id: str) -> list[int]:
        directory = self.definition_dir(experiment_id)
        if not directory.is_dir():
            return []
        versions: list[int] = []
        for path in directory.glob("v*.yaml"):
            try:
                versions.append(int(path.stem[1:]))
            except ValueError:
                continue
        return sorted(versions)

    def all(self) -> list[ExperimentDefinition]:
        return [self.get(experiment_id) for experiment_id in self.ids()]

    def results(self, experiment_id: str) -> list[ExperimentResult]:
        directory = self.results_dir(experiment_id)
        if not directory.is_dir():
            return []
        results: list[ExperimentResult] = []
        for path in sorted(directory.glob("run-*.yaml")):
            results.append(ExperimentResult.from_document(load_yaml_file(path), source=str(path)))
        return results

    def passing_results(self, experiment_id: str) -> list[ExperimentResult]:
        return [result for result in self.results(experiment_id) if result.verdict == "pass"]

    # --- writes ------------------------------------------------------------
    def _store_definition(
        self, document: dict[str, Any], *, expect_new: bool
    ) -> ExperimentDefinition:
        definition = ExperimentDefinition.from_document(document, source=str(document.get("id")))
        relative = f"definitions/{definition.id}/v{definition.version}.yaml"
        self.registry.put(
            definition.id,
            definition.raw,
            summary=definition.summary_row(),
            relative_path=relative,
            expect_new=expect_new,
            header=_DEFINITION_HEADER,
        )
        return definition

    def add(
        self,
        *,
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
        guardrail_metrics: list[dict[str, Any]] | None = None,
        environment_fingerprint: dict[str, str | None] | None = None,
        tool_versions: dict[str, str | None] | None = None,
        implementation_fingerprint: str | None = None,
        knowledge: tuple[str, ...] = (),
        now: str | None = None,
    ) -> ExperimentDefinition:
        record_id = self.registry.allocate_id()
        document = new_experiment_document(
            record_id=record_id,
            title=title,
            hypothesis=hypothesis,
            independent_variable=independent_variable,
            control=control,
            treatment=treatment,
            corpus=corpus,
            scorer=scorer,
            primary_metric=primary_metric,
            pass_threshold=pass_threshold,
            failure_threshold=failure_threshold,
            project=project,
            now=now or now_iso(),
            guardrail_metrics=guardrail_metrics,
            environment_fingerprint=environment_fingerprint,
            tool_versions=tool_versions,
            implementation_fingerprint=implementation_fingerprint,
            knowledge=knowledge,
        )
        return self._store_definition(document, expect_new=True)

    def freeze(self, experiment_id: str, *, now: str | None = None) -> ExperimentDefinition:
        """Freeze the current version and record its hash.

        Freezing is what makes results admissible. It must happen before any
        treatment measurement is inspected; the kernel enforces the ordering by
        refusing to record results against an unfrozen definition.
        """
        definition = self.get(experiment_id)
        if definition.frozen:
            if not definition.hash_matches():
                raise IntegrityError(
                    f"{experiment_id} v{definition.version} is frozen but its content no longer "
                    "matches its recorded hash; it was edited after freezing"
                )
            return definition
        document = dict(definition.raw)
        timestamp = now or now_iso()
        document["frozen"] = True
        document["frozen_at"] = timestamp
        document["updated_at"] = timestamp
        document["definition_hash"] = None
        document["definition_hash"] = definition_hash(document)
        return self._store_definition(document, expect_new=False)

    def revise(
        self,
        experiment_id: str,
        *,
        changes: Mapping[str, Any],
        reason: str,
        now: str | None = None,
    ) -> ExperimentDefinition:
        """Create the next version of a definition, leaving the current one intact.

        This is the only way to change a gate after freezing. The previous
        version's file is never rewritten, so results recorded against it remain
        interpretable.
        """
        if not reason.strip():
            raise ValidationError("a revision must state why the definition changed")
        current = self.get(experiment_id)
        protected = {"id", "version", "schema_version", "created_at", "supersedes_version"}
        invalid = protected.intersection(changes)
        if invalid:
            raise ValidationError(f"a revision may not change {', '.join(sorted(invalid))}")
        timestamp = now or now_iso()
        document = dict(current.raw)
        document.update(changes)
        document["version"] = current.version + 1
        document["supersedes_version"] = current.version
        document["revision_reason"] = reason
        document["created_at"] = timestamp
        document["updated_at"] = timestamp
        document["frozen"] = False
        document["frozen_at"] = None
        document["definition_hash"] = None
        return self._store_definition(document, expect_new=False)

    def record_result(
        self,
        experiment_id: str,
        *,
        primary_metric_value: float,
        control_metrics: Mapping[str, float],
        treatment_metrics: Mapping[str, float],
        interpretation: str,
        started_at: str,
        finished_at: str | None = None,
        guardrail_values: Mapping[str, float] | None = None,
        evidence: Sequence[str] = (),
        environment: Mapping[str, str | None] | None = None,
    ) -> ExperimentResult:
        """Record one run against the frozen current version.

        The caller never supplies the verdict — it is derived here from the
        frozen thresholds.
        """
        definition = self.get(experiment_id)
        if not definition.frozen:
            raise ValidationError(
                f"{experiment_id} v{definition.version} is not frozen; freeze the definition "
                "before recording results so the gates cannot move afterwards"
            )
        if not definition.hash_matches():
            raise IntegrityError(
                f"{experiment_id} v{definition.version} was edited after it was frozen; "
                "results recorded against it would be meaningless. Use 'experiment revise'."
            )

        values = {str(k): float(v) for k, v in (guardrail_values or {}).items()}
        violations = find_guardrail_violations(definition.guardrail_metrics, values)
        verdict = evaluate_verdict(
            direction=str(definition.primary_metric["direction"]),
            value=float(primary_metric_value),
            pass_threshold=definition.pass_threshold,
            failure_threshold=definition.failure_threshold,
            guardrail_violations=violations,
        )

        directory = self.results_dir(experiment_id)
        existing = sorted(directory.glob("run-*.yaml")) if directory.is_dir() else []
        run_number = len(existing) + 1
        run_id = f"{experiment_id}#run-{run_number:04d}"
        path = self.layout.require_inside(directory / f"run-{run_number:04d}.yaml")
        if path.exists():
            raise IntegrityError(f"{path} already exists; refusing to overwrite a recorded run")

        document = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "run_id": run_id,
            "experiment": experiment_id,
            "experiment_version": definition.version,
            "definition_hash": definition.stored_hash,
            "started_at": started_at,
            "finished_at": finished_at or now_iso(),
            "control_metrics": {str(k): float(v) for k, v in control_metrics.items()},
            "treatment_metrics": {str(k): float(v) for k, v in treatment_metrics.items()},
            "primary_metric_value": float(primary_metric_value),
            "guardrail_values": values,
            "guardrail_violations": violations,
            "verdict": verdict,
            "interpretation": interpretation,
            "evidence": sorted(set(evidence)),
            "environment": dict(environment or {}),
        }
        result = ExperimentResult.from_document(document, source=run_id)
        write_yaml_file(path, result.raw, header=_RESULT_HEADER)
        return result

    # --- integrity ---------------------------------------------------------
    def verify(self) -> list[ExperimentFinding]:
        findings: list[ExperimentFinding] = []
        for experiment_id in self.ids():
            try:
                definition = self.get(experiment_id)
            except (ValidationError, IntegrityError) as exc:
                findings.append(ExperimentFinding(experiment_id, f"definition unreadable: {exc}"))
                continue
            if definition.frozen and not definition.hash_matches():
                findings.append(
                    ExperimentFinding(
                        experiment_id,
                        f"v{definition.version} is frozen but its content no longer matches its "
                        "recorded hash (edited after freezing)",
                    )
                )
            known_versions = set(self.versions(experiment_id))
            for result in self.results(experiment_id):
                if result.experiment_version not in known_versions:
                    findings.append(
                        ExperimentFinding(
                            experiment_id,
                            f"{result.run_id} cites version {result.experiment_version}, which has "
                            "no definition file",
                        )
                    )
                    continue
                cited = self.get_version(experiment_id, result.experiment_version)
                if cited.stored_hash != result.definition_hash:
                    findings.append(
                        ExperimentFinding(
                            experiment_id,
                            f"{result.run_id} was recorded against a different definition than the "
                            f"v{result.experiment_version} file now on disk",
                        )
                    )
        return findings
