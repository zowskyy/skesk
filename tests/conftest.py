"""Shared fixtures.

The ``layout`` fixture calls the real :func:`skillkernel.project.bootstrap.initialize`.
It used to hand-roll the same steps, which meant every test was set up by
something no real consumer would ever run. Deleting that duplication is part of
what Vertical Slice 1 bought: the fixture now exercises the same entry point a
user does, so a bug in initialization fails the suite instead of hiding behind
it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from skillkernel.core.paths import Layout
from skillkernel.discovery.observations import ObservationStore
from skillkernel.evidence.ledger import EvidenceLedger
from skillkernel.experiments.store import ExperimentStore
from skillkernel.knowledge.store import KnowledgeStore
from skillkernel.project.bootstrap import initialize
from skillkernel.skills.store import SkillStore

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
    return initialize(tmp_path / "workspace", project_name="fixture-project", now=frozen_now)


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


@pytest.fixture
def skills(layout: Layout) -> SkillStore:
    return SkillStore(layout)
