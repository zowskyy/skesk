"""Source provenance: the write side and the read side of the same contract.

``provenance.x_source`` is written only by the bundle installer. But a skill
record is a plain file, so the installer being careful proves nothing on its
own -- a record can be hand-edited into claiming a source it never had. These
tests cover both halves: what the installer is allowed to write, and what
``doctor`` refuses to accept when reading it back.
"""

from __future__ import annotations

from typing import Any

import pytest
import yaml
from skillkernel.bundles.installer import install_bundle_by_id
from skillkernel.bundles.model import SOURCE_FIELD, is_content_hash, validate_x_source
from skillkernel.core.errors import ValidationError
from skillkernel.core.paths import Layout
from skillkernel.skills.store import SkillStore
from skillkernel.validation.doctor import run_doctor

PACKAGED = "two-method-escalation"
GOOD_HASH = "sha256:" + "a" * 64


def valid_source() -> dict[str, str]:
    return {"bundle_id": "demo", "bundle_version": "1.0.0", "content_hash": GOOD_HASH}


# --- the validator ---------------------------------------------------------


def test_a_well_formed_block_is_accepted() -> None:
    assert validate_x_source(valid_source(), source="test") == valid_source()


@pytest.mark.parametrize("field", ["bundle_id", "bundle_version", "content_hash"])
def test_every_field_is_required(field: str) -> None:
    block = valid_source()
    del block[field]
    with pytest.raises(ValidationError, match=field):
        validate_x_source(block, source="test")


def test_a_fourth_field_is_refused() -> None:
    """Three fields answer three questions. A fourth is unaudited state."""
    with pytest.raises(ValidationError):
        validate_x_source({**valid_source(), "trusted": True}, source="test")


@pytest.mark.parametrize(
    "value",
    ["", "sha256:short", "a" * 64, "md5:" + "a" * 32, "sha256:" + "A" * 64, "sha256:" + "z" * 64],
)
def test_a_content_hash_that_is_not_one_is_refused(value: str) -> None:
    block = {**valid_source(), "content_hash": value}
    with pytest.raises(ValidationError):
        validate_x_source(block, source="test")


@pytest.mark.parametrize("value", [None, [], "a string", 42])
def test_a_block_that_is_not_a_mapping_is_refused(value: Any) -> None:
    with pytest.raises(ValidationError):
        validate_x_source(value, source="test")


def test_is_content_hash_recognizes_only_the_declared_form() -> None:
    assert is_content_hash(GOOD_HASH)
    assert not is_content_hash("sha256:" + "a" * 63)
    assert not is_content_hash(None)


# --- what doctor does with it ----------------------------------------------


def forge_source(layout: Layout, skill_id: str, value: Any) -> None:
    """Hand-edit a record's source block, the way a person with an editor would."""
    path = SkillStore(layout).registry.path_of(skill_id)
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if value is None:
        document["provenance"].pop(SOURCE_FIELD, None)
    else:
        document["provenance"][SOURCE_FIELD] = value
    path.write_text(yaml.safe_dump(document), encoding="utf-8")


def test_a_skill_with_no_source_block_is_not_a_finding(layout: Layout, skills: SkillStore) -> None:
    """Most skills are authored locally and have no external source to declare."""
    skills.create(name="Locally authored", scope="project")
    report = run_doctor(layout)
    assert not report.has_errors


def test_an_honest_installed_skill_is_not_a_finding(layout: Layout) -> None:
    install_bundle_by_id(layout, PACKAGED)
    assert not run_doctor(layout).has_errors


@pytest.mark.parametrize(
    "forged",
    [
        {"bundle_id": "x", "bundle_version": "1", "content_hash": "not-a-hash"},
        {"bundle_id": "x", "bundle_version": "1"},
        {"bundle_id": "x", "bundle_version": "1", "content_hash": GOOD_HASH, "trusted": True},
        {"bundle_id": "", "bundle_version": "1", "content_hash": GOOD_HASH},
        "a string",
        [],
    ],
)
def test_doctor_reports_a_forged_source_block(layout: Layout, forged: Any) -> None:
    record = install_bundle_by_id(layout, PACKAGED).record
    forge_source(layout, record.id, forged)

    report = run_doctor(layout)
    assert report.is_complete, "the check crashed instead of reporting"
    findings = [f for f in report.sorted_findings() if f.code == "skill-source"]
    assert findings, [str(f) for f in report.sorted_findings()]


def test_doctor_stays_read_only_when_it_finds_a_forged_block(layout: Layout) -> None:
    record = install_bundle_by_id(layout, PACKAGED).record
    forge_source(layout, record.id, {"bundle_id": "x"})

    path = SkillStore(layout).registry.path_of(record.id)
    before = path.read_bytes()
    run_doctor(layout)
    assert path.read_bytes() == before, "doctor repaired a record instead of reporting it"
