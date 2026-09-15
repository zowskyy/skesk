"""Workspace initialization.

Turns an arbitrary directory into a SkillKernel repository: managed directories,
kernel configuration, an empty project profile, and one registry per domain.

Deliberately narrow for now. It refuses to overwrite an existing workspace, but
the full ``skillkernel init`` behaviour — reporting what it created versus left
alone, repairing a partial workspace, and the ``doctor`` integrity sweep — is
Vertical Slice 3. This function exists because the lifecycle acceptance test
needs a real workspace, and nothing more.

The profile is created empty. A default that guessed at domains or objectives
would be a fabricated requirement, and the kernel must not fabricate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from skillkernel import __version__
from skillkernel.core.clock import now_iso
from skillkernel.core.config import default_config_document, write_config
from skillkernel.core.errors import UnsafeOperationError
from skillkernel.core.paths import CONFIG_FILENAME, Layout
from skillkernel.discovery.observations import ObservationStore
from skillkernel.evidence.ledger import EvidenceLedger
from skillkernel.experiments.store import ExperimentStore
from skillkernel.knowledge.store import KnowledgeStore
from skillkernel.project.profile import (
    default_profile_document,
    write_profile,
    write_profile_markdown,
)
from skillkernel.skills.store import skills_registry

__all__ = ["InitializationReport", "initialize", "is_initialized"]


@dataclass
class InitializationReport:
    """What initialization actually did, so a caller can report it truthfully."""

    root: Path
    layout: Layout
    created_directories: list[str] = field(default_factory=list)
    created_files: list[str] = field(default_factory=list)
    created_registries: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"Initialized SkillKernel at {self.root}: "
            f"{len(self.created_directories)} directories, "
            f"{len(self.created_files)} files, "
            f"{len(self.created_registries)} registries."
        )


def is_initialized(root: Path) -> bool:
    return (Path(root) / CONFIG_FILENAME).is_file()


def initialize(
    root: Path,
    *,
    project_name: str,
    kernel_version: str | None = None,
    now: str | None = None,
) -> Layout:
    """Initialize a workspace at ``root`` and return its :class:`Layout`."""
    return initialize_with_report(
        root, project_name=project_name, kernel_version=kernel_version, now=now
    ).layout


def initialize_with_report(
    root: Path,
    *,
    project_name: str,
    kernel_version: str | None = None,
    now: str | None = None,
) -> InitializationReport:
    """Initialize a workspace, reporting everything that was created."""
    target = Path(root)
    if is_initialized(target):
        raise UnsafeOperationError(
            f"{target} already contains {CONFIG_FILENAME}; refusing to overwrite an existing "
            "SkillKernel workspace"
        )
    if target.exists() and not target.is_dir():
        raise UnsafeOperationError(f"{target} exists and is not a directory")

    target.mkdir(parents=True, exist_ok=True)
    layout = Layout(root=target.resolve())
    report = InitializationReport(root=layout.root, layout=layout)
    timestamp = now or now_iso()

    for directory in layout.managed_directories():
        if not directory.exists():
            directory.mkdir(parents=True, exist_ok=True)
            report.created_directories.append(layout.relative(directory))

    write_config(layout, default_config_document(kernel_version or __version__))
    report.created_files.append(CONFIG_FILENAME)

    profile = write_profile(layout, default_profile_document(project_name, now=timestamp))
    report.created_files.append(layout.relative(layout.profile_file))
    write_profile_markdown(layout, profile)
    report.created_files.append(layout.relative(layout.profile_doc))

    for name, registry in (
        ("knowledge", KnowledgeStore(layout).registry),
        ("experiments", ExperimentStore(layout).registry),
        ("evidence", EvidenceLedger(layout).registry),
        ("observations", ObservationStore(layout).registry),
        ("skills", skills_registry(layout)),
    ):
        registry.create()
        report.created_registries.append(name)

    return report
