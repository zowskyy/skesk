"""Shared fixtures.

The ``layout`` fixture calls the real :func:`skillkernel.project.bootstrap.initialize`.
It used to hand-roll the same steps, which meant every test was set up by
something no real consumer would ever run. Deleting that duplication is part of
what Vertical Slice 1 bought: the fixture now exercises the same entry point a
user does, so a bug in initialization fails the suite instead of hiding behind
it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from itertools import count
from pathlib import Path
from typing import Any

import pytest
import yaml
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


# --- synthetic bundles -----------------------------------------------------
#
# A bundle's refusal paths are about malformed, forged and colliding content,
# none of which can be shipped inside the package. These build one on disk, and
# ``load_bundle(..., root=...)`` reads it through the same code path that reads
# a packaged one.

VALID_MANIFEST: dict[str, Any] = {
    "schema_version": 1,
    "bundle_id": "demo-skill",
    "bundle_version": "1.0.0",
    "content_hash": None,
    "name": "Demo skill",
    "slug": "demo-skill",
    "scope": "core",
    "purpose": "Demonstrate a portable definition.",
    "applies_when": ["a demonstration is needed"],
    "do_not_apply_when": ["nothing is being demonstrated"],
    "activation_rules": {
        "require_any": ["demo"],
        "require_all": [],
        "exclude_any": ["production"],
    },
    "inputs": ["a request"],
    "preconditions": ["the workspace is initialized"],
    "procedure": ["do the demonstrable thing"],
    "success_conditions": ["the thing was demonstrated"],
    "failure_modes": ["the thing was not demonstrated"],
    "verification": ["look at the result"],
    "evaluation": {
        "corpus_id": "demo-corpus",
        "pass_threshold": 0.8,
        "max_false_activation_rate": 0.2,
        "description": None,
    },
}

VALID_POSITIVE: list[dict[str, Any]] = [
    {"schema_version": 1, "case_id": "demo-yes", "signals": ["demo"], "description": None}
]
VALID_NEGATIVE: list[dict[str, Any]] = [
    {"schema_version": 1, "case_id": "demo-no", "signals": ["production"], "description": None}
]


@pytest.fixture
def bundle_manifest() -> dict[str, Any]:
    """A deep copy of the reference manifest, safe for a test to mutate."""
    return deepcopy(VALID_MANIFEST)


@pytest.fixture
def bundle_positive() -> list[dict[str, Any]]:
    return deepcopy(VALID_POSITIVE)


@pytest.fixture
def bundle_negative() -> list[dict[str, Any]]:
    return deepcopy(VALID_NEGATIVE)


@pytest.fixture
def make_bundle(tmp_path: Path) -> Callable[..., Path]:
    """Build a bundle tree on disk and return the root to read it from."""
    counter = count(1)

    def build(
        *,
        manifest: dict[str, Any] | None = None,
        positive: Sequence[Mapping[str, Any]] | None = None,
        negative: Sequence[Mapping[str, Any]] | None = None,
        directory_name: str | None = None,
        manifest_text: str | None = None,
    ) -> Path:
        document = dict(VALID_MANIFEST if manifest is None else manifest)
        name = directory_name or str(document.get("bundle_id", f"bundle-{next(counter)}"))
        root = tmp_path / f"bundles-{next(counter)}"
        directory = root / name
        (directory / "examples" / "positive").mkdir(parents=True)
        (directory / "examples" / "negative").mkdir(parents=True)

        text = manifest_text if manifest_text is not None else yaml.safe_dump(document)
        (directory / "bundle.yaml").write_text(text, encoding="utf-8")

        for polarity, cases in (
            ("positive", VALID_POSITIVE if positive is None else positive),
            ("negative", VALID_NEGATIVE if negative is None else negative),
        ):
            for case in cases:
                path = directory / "examples" / polarity / f"{case['case_id']}.yaml"
                path.write_text(yaml.safe_dump(dict(case)), encoding="utf-8")
        return root

    return build
