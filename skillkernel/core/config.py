"""Kernel configuration (``skillkernel.yaml``).

Configuration holds *kernel policy* — discovery thresholds, promotion gates —
and is deliberately separate from the project profile, which holds *project
facts*. Mixing them would make a threshold change look like a change in what
the project is.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from skillkernel.core.paths import CONFIG_FILENAME, Layout
from skillkernel.core.schema import Schema, int_spec, object_spec, str_spec
from skillkernel.core.yamlio import load_yaml_file, write_yaml_file

__all__ = ["CONFIG_SCHEMA", "KernelConfig", "default_config_document", "load_config"]

CONFIG_SCHEMA_VERSION = 1

CONFIG_SCHEMA = Schema(
    name="kernel-config",
    supported_versions=(CONFIG_SCHEMA_VERSION,),
    root=object_spec(
        {
            "schema_version": int_spec(required=True),
            "kernel": object_spec(
                {
                    "version": str_spec(required=True, min_length=1),
                },
                required=True,
                unknown="allow_extension",
            ),
            "discovery": object_spec(
                {
                    "min_occurrences": int_spec(
                        required=True,
                        minimum=2,
                        description=(
                            "Times a failure class must recur before seeding a candidate."
                        ),
                    ),
                    "min_successful_corrections": int_spec(
                        required=True,
                        minimum=1,
                        description="Times the same corrective procedure must have succeeded.",
                    ),
                },
                required=True,
                unknown="allow_extension",
            ),
            "promotion": object_spec(
                {
                    "min_trusted_distinct_corpora": int_spec(
                        required=True,
                        minimum=2,
                        description="Distinct evaluation corpora required for 'trusted'.",
                    ),
                    "cross_project_min_projects": int_spec(
                        required=True,
                        minimum=2,
                        description="Distinct projects required before a skill may become core.",
                    ),
                },
                required=True,
                unknown="allow_extension",
            ),
        },
        unknown="allow_extension",
    ),
)


@dataclass(frozen=True)
class KernelConfig:
    """Validated kernel policy."""

    kernel_version: str
    min_occurrences: int
    min_successful_corrections: int
    min_trusted_distinct_corpora: int
    cross_project_min_projects: int
    raw: dict[str, Any]

    @classmethod
    def from_document(cls, document: Any, *, source: str | None = None) -> KernelConfig:
        data = dict(CONFIG_SCHEMA.validate(document, source=source))
        discovery = data["discovery"]
        promotion = data["promotion"]
        return cls(
            kernel_version=str(data["kernel"]["version"]),
            min_occurrences=int(discovery["min_occurrences"]),
            min_successful_corrections=int(discovery["min_successful_corrections"]),
            min_trusted_distinct_corpora=int(promotion["min_trusted_distinct_corpora"]),
            cross_project_min_projects=int(promotion["cross_project_min_projects"]),
            raw=data,
        )


def default_config_document(kernel_version: str) -> dict[str, Any]:
    """The configuration ``skillkernel init`` writes into a fresh repository."""
    return {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "kernel": {"version": kernel_version},
        "discovery": {
            "min_occurrences": 3,
            "min_successful_corrections": 2,
        },
        "promotion": {
            "min_trusted_distinct_corpora": 2,
            "cross_project_min_projects": 3,
        },
    }


def load_config(layout: Layout) -> KernelConfig:
    document = load_yaml_file(layout.config_file)
    return KernelConfig.from_document(document, source=CONFIG_FILENAME)


def write_config(layout: Layout, document: dict[str, Any]) -> None:
    KernelConfig.from_document(document, source=CONFIG_FILENAME)
    write_yaml_file(
        layout.config_file,
        document,
        header=(
            "# SkillKernel configuration. Presence of this file marks the repository root.\n"
            "# Kernel policy lives here; project facts live in docs/project/profile.yaml.\n"
        ),
    )


def config_path(root: Path) -> Path:
    return root / CONFIG_FILENAME
