"""The skill contract, the field-role classification, and activation boundaries."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from skillkernel.core.errors import ValidationError
from skillkernel.skills.model import (
    BEHAVIORAL_ROLE,
    FIELD_ROLES,
    SKILL_SCHEMA,
    SkillRecord,
    behavior_fingerprint,
    new_skill_document,
)

NOW = "2024-01-31T12:00:00Z"


def base_document(**overrides: Any) -> dict[str, Any]:
    document = new_skill_document(
        record_id="SKILL-0001",
        name="Deterministic frame rendering",
        slug="deterministic-frame-rendering",
        scope="project",
        now=NOW,
        created_from=["OBS-0001"],
        purpose="Derive animation advancement from frame number, not wall-clock time.",
        applies_when=["rendering a deterministic sequence"],
        do_not_apply_when=["rendering an interactive preview"],
        activation_rules={"require_any": ["deterministic-render"], "exclude_any": ["interactive"]},
    )
    document.update(overrides)
    return document


def record(**overrides: Any) -> SkillRecord:
    return SkillRecord.from_document(base_document(**overrides))


# --- contract --------------------------------------------------------------


def test_a_generated_document_is_schema_valid_and_starts_observed() -> None:
    skill = record()
    assert skill.maturity == "observed"
    assert skill.scope == "project"
    assert skill.id == "SKILL-0001"


@pytest.mark.parametrize("field", sorted(SKILL_SCHEMA.root.fields))
def test_every_declared_field_is_required_to_be_present(field: str) -> None:
    """The contract fixes the *shape*; maturity gates fix the content."""
    document = base_document()
    if not SKILL_SCHEMA.root.fields[field].required:
        pytest.skip(f"{field} is optional by design")
    document.pop(field, None)
    assert not SKILL_SCHEMA.is_valid(document)


def test_activation_boundaries_are_part_of_the_contract() -> None:
    """``do_not_apply_when`` is a declared field, not an afterthought."""
    assert SKILL_SCHEMA.root.fields["do_not_apply_when"].required
    assert SKILL_SCHEMA.root.fields["activation_rules"].required


def test_an_unknown_top_level_field_is_rejected() -> None:
    assert not SKILL_SCHEMA.is_valid(base_document(surprise=1))


def test_an_extension_prefixed_field_is_accepted() -> None:
    assert SKILL_SCHEMA.is_valid(base_document(x_vendor={"anything": True}))


def test_an_unknown_maturity_is_rejected() -> None:
    with pytest.raises(ValidationError):
        record(classification={"scope": "project", "maturity": "legendary"})


def test_an_unknown_scope_is_rejected() -> None:
    with pytest.raises(ValidationError):
        record(classification={"scope": "universal", "maturity": "observed"})


def test_duplicate_activation_rule_entries_are_rejected() -> None:
    with pytest.raises(ValidationError):
        record(
            activation_rules={
                "require_any": ["a", "a"],
                "require_all": [],
                "exclude_any": [],
            }
        )


def test_activation_rules_must_be_lists_of_strings() -> None:
    with pytest.raises(ValidationError):
        record(
            activation_rules={"require_any": "deterministic", "require_all": [], "exclude_any": []}
        )


def test_a_malformed_evidence_reference_is_rejected() -> None:
    with pytest.raises(ValidationError):
        record(evidence={"experiments": ["EXP-1"], "knowledge": [], "records": []})


def test_a_knowledge_id_in_the_experiments_slot_is_rejected() -> None:
    with pytest.raises(ValidationError):
        record(evidence={"experiments": ["K-0001"], "knowledge": [], "records": []})


# --- field-role classification --------------------------------------------


def test_every_schema_field_has_a_declared_role() -> None:
    """Adding a field without classifying it must fail here, not silently pass."""
    assert set(SKILL_SCHEMA.root.fields) == set(FIELD_ROLES)


def test_every_role_is_one_of_the_four_defined_kinds() -> None:
    assert set(FIELD_ROLES.values()) <= {
        "identity",
        "presentation",
        BEHAVIORAL_ROLE,
        "lifecycle",
        "provenance",
    }


BEHAVIORAL_CHANGES: dict[str, Any] = {
    "purpose": "An entirely different purpose.",
    "applies_when": ["some other condition"],
    "do_not_apply_when": ["some other exclusion"],
    "activation_rules": {"require_any": ["other"], "require_all": [], "exclude_any": []},
    "inputs": ["a new input"],
    "preconditions": ["a new precondition"],
    "procedure": ["step one"],
    "success_conditions": ["it worked"],
    "failure_modes": ["it did not"],
    "verification": ["check the output"],
}

NON_BEHAVIORAL_CHANGES: dict[str, Any] = {
    "name": "A Renamed Skill",
    "version": "9.9.9",
    "classification": {"scope": "core", "maturity": "trusted"},
    "confidence": "high",
    "updated_at": "2030-01-01T00:00:00Z",
    "project_scope": {"origin_project": "other", "validated_in_projects": ["a", "b"]},
    "evidence": {"experiments": ["EXP-0001"], "knowledge": ["K-0001"], "records": ["EV-0001"]},
    "provenance": {
        "created_from": ["OBS-0002"],
        "created_by": "discovery",
        "created_at": "2030-01-01T00:00:00Z",
        "discovery_key": "some::key",
    },
    "deprecation": {"reason": "superseded", "replaced_by": None, "at": NOW},
}


def test_the_behavioural_change_set_covers_every_behavioural_field() -> None:
    behavioural = {field for field, role in FIELD_ROLES.items() if role == BEHAVIORAL_ROLE}
    assert set(BEHAVIORAL_CHANGES) == behavioural


@pytest.mark.parametrize("field", sorted(BEHAVIORAL_CHANGES))
def test_changing_a_behavioural_field_changes_the_fingerprint(field: str) -> None:
    original = base_document()
    changed = copy.deepcopy(original)
    changed[field] = BEHAVIORAL_CHANGES[field]
    assert behavior_fingerprint(changed) != behavior_fingerprint(original)


@pytest.mark.parametrize("field", sorted(NON_BEHAVIORAL_CHANGES))
def test_changing_a_non_behavioural_field_leaves_the_fingerprint_alone(field: str) -> None:
    """Promoting or annotating a skill must not invalidate its evaluations."""
    original = base_document()
    changed = copy.deepcopy(original)
    changed[field] = NON_BEHAVIORAL_CHANGES[field]
    assert behavior_fingerprint(changed) == behavior_fingerprint(original)


def test_the_non_behavioural_change_set_covers_every_non_behavioural_field() -> None:
    non_behavioural = {
        field
        for field, role in FIELD_ROLES.items()
        if role != BEHAVIORAL_ROLE and field not in {"id", "slug", "schema_version"}
    }
    assert set(NON_BEHAVIORAL_CHANGES) == non_behavioural


def test_promotion_from_candidate_to_trusted_preserves_the_fingerprint() -> None:
    """The concrete circularity this design avoids."""
    candidate = base_document()
    promoted = copy.deepcopy(candidate)
    promoted["classification"] = {"scope": "project", "maturity": "trusted"}
    promoted["evidence"] = {"experiments": ["EXP-0001"], "knowledge": [], "records": ["EV-0007"]}
    promoted["updated_at"] = "2030-01-01T00:00:00Z"
    assert behavior_fingerprint(promoted) == behavior_fingerprint(candidate)


def test_the_fingerprint_is_stable_across_key_ordering() -> None:
    original = base_document()
    reordered = dict(reversed(list(original.items())))
    assert behavior_fingerprint(reordered) == behavior_fingerprint(original)


def test_the_content_hash_differs_from_the_behaviour_fingerprint() -> None:
    skill = record()
    assert skill.content_hash() != skill.fingerprint()


def test_the_content_hash_does_notice_a_lifecycle_change() -> None:
    before = record()
    after = record(confidence="high")
    assert before.content_hash() != after.content_hash()
    assert before.fingerprint() == after.fingerprint()


# --- activation boundaries -------------------------------------------------


def test_require_any_activates_on_any_listed_signal() -> None:
    skill = record(
        activation_rules={"require_any": ["a", "b"], "require_all": [], "exclude_any": []}
    )
    assert skill.applies_to(["b"])
    assert not skill.applies_to(["c"])


def test_require_all_demands_every_listed_signal() -> None:
    skill = record(
        activation_rules={"require_any": [], "require_all": ["a", "b"], "exclude_any": []}
    )
    assert skill.applies_to(["a", "b", "c"])
    assert not skill.applies_to(["a"])


def test_exclusions_win_over_inclusions() -> None:
    """Over-activation is the dangerous direction, so exclusion is decisive."""
    skill = record(
        activation_rules={"require_any": ["a"], "require_all": [], "exclude_any": ["stop"]}
    )
    assert skill.applies_to(["a"])
    assert not skill.applies_to(["a", "stop"])


def test_a_skill_with_no_activation_rules_never_activates() -> None:
    """Fail closed: an unconfigured skill must not apply to everything."""
    skill = record(activation_rules={"require_any": [], "require_all": [], "exclude_any": []})
    assert not skill.applies_to([])
    assert not skill.applies_to(["anything", "at", "all"])


def test_activation_matching_ignores_case_and_surrounding_whitespace() -> None:
    skill = record(
        activation_rules={"require_any": ["Deterministic"], "require_all": [], "exclude_any": []}
    )
    assert skill.applies_to(["  deterministic  "])


def test_activation_with_no_signals_at_all_is_false() -> None:
    assert not record().applies_to([])


def test_combined_rules_require_both_halves() -> None:
    skill = record(
        activation_rules={"require_any": ["a", "b"], "require_all": ["core"], "exclude_any": []}
    )
    assert skill.applies_to(["a", "core"])
    assert not skill.applies_to(["a"])
    assert not skill.applies_to(["core"])
