"""Shared fixtures.

The fixtures below build a SkillKernel repository using only public APIs
(``Layout``, ``write_config``, ``Registry.create``). There is no ``skillkernel
init`` yet, and tests must not reach into registry internals to compensate --
the moment a test needs to hand-edit an index to set up, that is a signal the
public API is missing something.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from skillkernel.core.config import default_config_document, write_config
from skillkernel.core.paths import Layout
from skillkernel.discovery.observations import ObservationStore
from skillkernel.evidence.ledger import EvidenceLedger
from skillkernel.experiments.store import ExperimentStore
from skillkernel.knowledge.store import KnowledgeStore

FROZEN_NOW = "2024-01-31T12:00:00Z"
LATER = "2024-02-01T12:00:00Z"


@pytest.fixture
def frozen_now(monkeypatch: pytest.MonkeyPatch) -> str:
    """Pin ``now_iso()`` so records are byte-for-byte reproducible."""
    monkeypatch.setenv("SKILLKERNEL_NOW", FROZEN_NOW)
    return FROZEN_NOW


@pytest.fixture
def layout(tmp_path: Path, frozen_now: str) -> Layout:
    """An initialized, empty SkillKernel repository in a temporary directory."""
    built = Layout(root=tmp_path)
    for directory in built.managed_directories():
        directory.mkdir(parents=True, exist_ok=True)
    write_config(built, default_config_document("0.1.0-test"))
    KnowledgeStore(built).registry.create()
    ExperimentStore(built).registry.create()
    EvidenceLedger(built).registry.create()
    ObservationStore(built).registry.create()
    return built


@pytest.fixture
def knowledge(layout: Layout) -> KnowledgeStore:
    return KnowledgeStore(layout)


@pytest.fixture
def experiments(layout: Layout) -> ExperimentStore:
    return ExperimentStore(layout)


@pytest.fixture
def ledger(layout: Layout) -> EvidenceLedger:
    return EvidenceLedger(layout)


@pytest.fixture
def observations(layout: Layout) -> ObservationStore:
    return ObservationStore(layout)
