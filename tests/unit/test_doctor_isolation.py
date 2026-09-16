"""One corrupt skill must not hide every other skill's findings.

``doctor`` states the rule in its own source -- *"Per-skill checks are isolated:
an unreadable record ... must not abort the remaining skills"* -- but two loops
did not honour it, and adversarial review after the VS4 freeze exposed both.

``_check_skill_locations`` computed the canonical path **outside** its per-record
``try``, so a record that loaded fine but carried a malformed slug raised out of
the loop. ``_check_skills`` had no isolation at all.

In both cases the outer ``_guard`` did its job -- a ``SkillKernelError`` became
an ERROR finding and the exit code stayed 1, so Codex's reported exit-70
mechanism was wrong. The real damage was quieter: the loop stopped, the finding
arrived unattributed, and every later skill went uninspected.

These tests fail at ``a402432``. They assert the surviving distinction too: a
genuinely unexpected exception must still be an internal error, not a finding.
"""

from __future__ import annotations

import json

import pytest
import yaml
from skillkernel.core.errors import SkillKernelError
from skillkernel.core.paths import Layout
from skillkernel.skills.store import SkillStore
from skillkernel.validation.doctor import run_doctor

MALFORMED_SLUG = "../../evil"


def corrupt_slug(store: SkillStore, skill_id: str, slug: str) -> None:
    """Model legacy on-disk corruption: a stored slug ``create()`` would now refuse.

    Written straight to the file on purpose. The public API correctly refuses
    this since DEC-0013, so the only way to produce the state ``doctor`` exists
    to diagnose is to persist it directly.
    """
    path = store.registry.path_of(skill_id)
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["slug"] = slug
    path.write_text(yaml.safe_dump(document), encoding="utf-8")


def codes(report: object) -> list[str]:
    return sorted({f.code for f in report.sorted_findings()})  # type: ignore[attr-defined]


def findings_for(report: object, skill_id: str) -> list[str]:
    return [f.code for f in report.sorted_findings() if f.location == skill_id]  # type: ignore[attr-defined]


# --- location check: a malformed record must not hide a later one ----------


def test_a_malformed_slug_does_not_hide_a_later_location_finding(
    layout: Layout, skills: SkillStore
) -> None:
    """The defect, stated as a test: A aborts the loop and B's finding vanishes."""
    a = skills.create(name="Alpha skill", scope="core")  # SKILL-0001, sorts first
    b = skills.create(name="Zeta skill", scope="core")  # SKILL-0002

    # B: a genuine, independently detectable location mismatch.
    path = skills.registry.path_of(b.id)
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["slug"] = "renamed-elsewhere"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")

    # A: malformed slug, processed first.
    corrupt_slug(skills, a.id, MALFORMED_SLUG)

    report = run_doctor(layout)

    assert report.is_complete, "a stored-state problem is a finding, not an internal error"
    assert not report.internal_errors
    assert findings_for(report, a.id), f"{a.id} was not attributed its own finding"
    assert findings_for(report, b.id), f"{b.id}'s real location finding was suppressed by {a.id}"


def test_a_malformed_slug_in_the_middle_does_not_hide_the_rest(
    layout: Layout, skills: SkillStore
) -> None:
    """Isolation must not depend on the corrupt record happening to be first."""
    a = skills.create(name="Alpha skill", scope="core")  # SKILL-0001, healthy
    b = skills.create(name="Middle skill", scope="core")  # SKILL-0002, malformed
    c = skills.create(name="Omega skill", scope="core")  # SKILL-0003, mislocated

    path = skills.registry.path_of(c.id)
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["slug"] = "renamed-elsewhere"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")

    corrupt_slug(skills, b.id, MALFORMED_SLUG)

    report = run_doctor(layout)
    assert report.is_complete
    assert not findings_for(report, a.id), "the healthy skill should have no finding"
    assert findings_for(report, b.id), "the malformed skill was not attributed"
    assert findings_for(report, c.id), "a later skill's finding was suppressed"


def test_the_malformed_slug_finding_is_attributed_and_located(
    layout: Layout, skills: SkillStore
) -> None:
    a = skills.create(name="Alpha skill", scope="core")
    corrupt_slug(skills, a.id, MALFORMED_SLUG)

    report = run_doctor(layout)
    matching = [f for f in report.sorted_findings() if f.location == a.id]
    assert matching, "the finding must name the skill it belongs to"
    assert any(f.code == "skill-location" for f in matching)


# --- the skills check: same invariant, same requirement -------------------


def test_an_unreadable_record_does_not_hide_a_later_history_finding(
    layout: Layout, skills: SkillStore
) -> None:
    """``_check_skills`` had no per-skill isolation whatsoever."""
    a = skills.create(name="Alpha skill", scope="core")  # SKILL-0001, made unreadable
    b = skills.create(name="Zeta skill", scope="core")  # SKILL-0002, missing history

    skills.history_path(b.id).unlink()
    skills.registry.path_of(a.id).write_text(
        "schema_version: 1\nid: SKILL-0001\n", encoding="utf-8"
    )

    report = run_doctor(layout)
    assert report.is_complete
    assert findings_for(report, b.id), f"{b.id}'s history finding was suppressed by {a.id}"


def test_a_damaged_skill_alone_still_yields_an_attributed_finding(
    layout: Layout, skills: SkillStore
) -> None:
    """Even with one skill, the finding must name it rather than the check."""
    b = skills.create(name="Zeta skill", scope="core")
    skills.history_path(b.id).unlink()

    report = run_doctor(layout)
    assert report.is_complete
    assert findings_for(report, b.id), "the damaged skill was not attributed its own finding"


# --- doctrine that must survive the repair --------------------------------


def test_ordinary_corruption_still_exits_one_through_the_cli(
    layout: Layout, skills: SkillStore
) -> None:
    from skillkernel.cli.exit_codes import INTEGRITY_FAILURE

    a = skills.create(name="Alpha skill", scope="core")
    corrupt_slug(skills, a.id, MALFORMED_SLUG)

    report = run_doctor(layout)
    assert report.has_errors
    assert report.is_complete
    expected = INTEGRITY_FAILURE if report.has_errors else 0
    assert expected == INTEGRITY_FAILURE


def test_an_unexpected_failure_is_still_an_internal_error_not_a_finding(
    layout: Layout, skills: SkillStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The repair narrows over-broad abortion; it must not erase the boundary.

    A ``SkillKernelError`` means the kernel looked and found a problem. Anything
    else means the kernel broke while looking, and that distinction is what
    exit 70 exists to carry (DEC-0010).
    """
    skills.create(name="Alpha skill", scope="core")

    def explode(self: SkillStore) -> list[str]:
        raise RuntimeError("simulated implementation fault")

    monkeypatch.setattr(SkillStore, "ids", explode)

    report = run_doctor(layout)
    assert report.internal_errors, "an unexpected exception must not become a finding"
    assert not report.is_complete, "a crashed check makes the report incomplete"
    assert all(f.code == "internal_error" for f in report.internal_errors)


def test_normalized_output_stays_workspace_independent(layout: Layout, skills: SkillStore) -> None:
    """DEC-0012 must survive the new attributed findings."""
    a = skills.create(name="Alpha skill", scope="core")
    corrupt_slug(skills, a.id, MALFORMED_SLUG)

    document = json.dumps(run_doctor(layout).to_document())
    assert str(layout.root) not in document, "the report leaked an absolute workspace path"


def test_findings_are_deterministically_ordered(layout: Layout, skills: SkillStore) -> None:
    a = skills.create(name="Alpha skill", scope="core")
    b = skills.create(name="Zeta skill", scope="core")
    corrupt_slug(skills, a.id, MALFORMED_SLUG)
    corrupt_slug(skills, b.id, MALFORMED_SLUG)

    first = [str(f) for f in run_doctor(layout).sorted_findings()]
    second = [str(f) for f in run_doctor(Layout(root=layout.root)).sorted_findings()]
    assert first == second
    assert len(first) >= 2, "each corrupt skill should produce its own finding"


def test_doctor_remains_read_only_on_a_corrupted_workspace(
    layout: Layout, skills: SkillStore
) -> None:
    import hashlib

    a = skills.create(name="Alpha skill", scope="core")
    corrupt_slug(skills, a.id, MALFORMED_SLUG)

    def snapshot() -> dict[str, str]:
        return {
            p.relative_to(layout.root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(layout.root.rglob("*"))
            if p.is_file()
        }

    before = snapshot()
    assert before
    run_doctor(layout)
    assert snapshot() == before, "doctor repaired a record instead of reporting it"


def test_a_healthy_workspace_still_reports_nothing(layout: Layout, skills: SkillStore) -> None:
    skills.create(name="Alpha skill", scope="core")
    report = run_doctor(layout)
    assert report.is_complete
    assert not report.has_errors, [str(f) for f in report.sorted_findings()]


def test_a_malformed_slug_does_not_crash_the_cli_with_a_traceback(
    layout: Layout, skills: SkillStore
) -> None:
    from skillkernel.core.errors import SkillKernelError as _SKE

    a = skills.create(name="Alpha skill", scope="core")
    corrupt_slug(skills, a.id, MALFORMED_SLUG)
    try:
        run_doctor(layout)
    except _SKE as exc:  # pragma: no cover - the point is that this does not happen
        pytest.fail(f"doctor raised instead of reporting: {exc}")


def test_skill_kernel_error_is_the_channel_for_stored_state_problems() -> None:
    """Guards the premise the repair rests on: ValidationError is a domain error."""
    from skillkernel.core.errors import ValidationError

    assert issubclass(ValidationError, SkillKernelError)
