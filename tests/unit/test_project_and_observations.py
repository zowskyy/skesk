"""Kernel configuration, the project profile, and observation records."""

from __future__ import annotations

from typing import Any

import pytest
from skillkernel.core.config import (
    KernelConfig,
    default_config_document,
    load_config,
    write_config,
)
from skillkernel.core.errors import ValidationError
from skillkernel.core.paths import Layout
from skillkernel.discovery.observations import ObservationStore
from skillkernel.project.profile import (
    ProjectProfile,
    default_profile_document,
    load_profile,
    render_profile_markdown,
    set_profile_values,
    write_profile,
    write_profile_markdown,
)

# --- kernel configuration --------------------------------------------------


def test_the_default_configuration_is_valid() -> None:
    config = KernelConfig.from_document(default_config_document("0.1.0"))
    assert config.min_occurrences >= 2
    assert config.cross_project_min_projects >= 2


def test_configuration_round_trips_through_the_repository(layout: Layout) -> None:
    assert load_config(layout).kernel_version == "0.1.0-test"


def test_an_unknown_configuration_section_is_rejected(layout: Layout) -> None:
    document = default_config_document("0.1.0")
    document["mystery"] = {"a": 1}
    with pytest.raises(ValidationError, match="unknown field"):
        write_config(layout, document)


def test_an_extension_prefixed_configuration_section_is_accepted(layout: Layout) -> None:
    document = default_config_document("0.1.0")
    document["x_house_style"] = {"a": 1}
    write_config(layout, document)
    assert load_config(layout).raw["x_house_style"] == {"a": 1}


def test_a_discovery_threshold_below_two_is_rejected() -> None:
    document = default_config_document("0.1.0")
    document["discovery"]["min_occurrences"] = 1
    with pytest.raises(ValidationError, match="must be >= 2"):
        KernelConfig.from_document(document)


def test_a_missing_promotion_section_is_rejected() -> None:
    document = default_config_document("0.1.0")
    del document["promotion"]
    with pytest.raises(ValidationError, match="promotion"):
        KernelConfig.from_document(document)


def test_writing_an_invalid_configuration_does_not_touch_the_file(layout: Layout) -> None:
    before = layout.config_file.read_bytes()
    with pytest.raises(ValidationError):
        write_config(layout, {"schema_version": 1})
    assert layout.config_file.read_bytes() == before


# --- project profile -------------------------------------------------------


def test_a_default_profile_invents_nothing(frozen_now: str) -> None:
    profile = ProjectProfile.from_document(default_profile_document("demo", now=frozen_now))
    assert profile.name == "demo"
    assert profile.domains == ()
    assert profile.objectives == ()
    assert profile.types == ()


def test_the_profile_schema_does_not_constrain_domain_vocabulary(frozen_now: str) -> None:
    """The kernel must serve a renderer and a payroll system equally."""
    document = default_profile_document("demo", now=frozen_now)
    document["domains"] = ["graphics", "animation"]
    document["project"]["type"] = ["desktop_application"]
    assert ProjectProfile.from_document(document).domains == ("graphics", "animation")

    other = default_profile_document("other", now=frozen_now)
    other["domains"] = ["payroll", "tax-compliance"]
    other["project"]["type"] = ["batch_job"]
    assert ProjectProfile.from_document(other).domains == ("payroll", "tax-compliance")


def test_verification_gates_accept_names_the_kernel_has_never_heard_of(
    frozen_now: str,
) -> None:
    document = default_profile_document("demo", now=frozen_now)
    document["verification"] = {
        "lint": "required",
        "fuzz": "optional",
        "mutation": "not_applicable",
    }
    profile = ProjectProfile.from_document(document)
    assert profile.required_gates() == ("lint",)


def test_an_invalid_verification_level_is_rejected(frozen_now: str) -> None:
    document = default_profile_document("demo", now=frozen_now)
    document["verification"] = {"lint": "probably"}
    with pytest.raises(ValidationError, match="must be one of"):
        ProjectProfile.from_document(document)


def test_duplicate_domains_are_rejected(frozen_now: str) -> None:
    document = default_profile_document("demo", now=frozen_now)
    document["domains"] = ["graphics", "graphics"]
    with pytest.raises(ValidationError, match="duplicate"):
        ProjectProfile.from_document(document)


def test_the_profile_round_trips_through_the_repository(layout: Layout, frozen_now: str) -> None:
    document = default_profile_document("demo", now=frozen_now)
    document["objectives"] = ["deterministic behaviour"]
    write_profile(layout, document)
    assert load_profile(layout).objectives == ("deterministic behaviour",)


def test_immutable_profile_fields_cannot_be_rewritten_by_an_update(frozen_now: str) -> None:
    document = default_profile_document("demo", now=frozen_now)
    for field in ["schema_version", "created_at"]:
        with pytest.raises(ValidationError, match="may not be modified"):
            set_profile_values(document, {field: "tampered"})


def test_an_update_refreshes_the_updated_at_stamp(frozen_now: str) -> None:
    document = default_profile_document("demo", now=frozen_now)
    updated = set_profile_values(document, {"domains": ["graphics"]}, now="2025-01-01T00:00:00Z")
    assert updated["updated_at"] == "2025-01-01T00:00:00Z"
    assert updated["created_at"] == frozen_now


def test_the_generated_profile_document_is_deterministic(frozen_now: str) -> None:
    profile = ProjectProfile.from_document(default_profile_document("demo", now=frozen_now))
    assert render_profile_markdown(profile) == render_profile_markdown(profile)


def test_the_generated_profile_document_declares_that_it_is_generated(
    layout: Layout, frozen_now: str
) -> None:
    profile = ProjectProfile.from_document(default_profile_document("demo", now=frozen_now))
    content = write_profile_markdown(layout, profile)
    assert content.startswith("<!-- GENERATED FILE - do not edit.")
    assert "docs/project/profile.yaml" in content
    assert layout.profile_doc.read_text() == content


def test_the_generated_document_reflects_recorded_facts(frozen_now: str) -> None:
    document = default_profile_document("demo", now=frozen_now)
    document["domains"] = ["graphics"]
    document["environment"] = {"operating_system": "linux"}
    rendered = render_profile_markdown(ProjectProfile.from_document(document))
    assert "- graphics" in rendered
    assert "`operating_system` | linux" in rendered


def test_empty_sections_say_so_rather_than_being_omitted(frozen_now: str) -> None:
    profile = ProjectProfile.from_document(default_profile_document("demo", now=frozen_now))
    assert render_profile_markdown(profile).count("_None recorded._") >= 4


# --- observations ----------------------------------------------------------


def add_observation(store: ObservationStore, **overrides: Any) -> Any:
    kwargs: dict[str, Any] = {
        "category": "failure",
        "task": "add a migration",
        "context": "alembic revision on a fresh database",
        "classification": "Missing Down Migration",
        "outcome": "resolved",
        "project": "demo",
        "procedure_used": "Write the downgrade() body before committing",
    }
    kwargs.update(overrides)
    return store.add(**kwargs)


def test_an_observation_round_trips(observations: ObservationStore) -> None:
    record = add_observation(observations)
    assert observations.get(record.id).task == "add a migration"


def test_grouping_keys_are_normalized_for_discovery(observations: ObservationStore) -> None:
    first = add_observation(observations, classification="Missing Down Migration")
    second = add_observation(observations, classification="  missing   down migration ")
    assert first.classification_key == second.classification_key
    assert first.classification != second.classification


def test_procedure_key_is_none_when_no_procedure_was_used(
    observations: ObservationStore,
) -> None:
    record = add_observation(observations, procedure_used=None, outcome="unresolved")
    assert record.procedure_key is None


@pytest.mark.parametrize("category", ["failure", "success", "workaround", "pattern"])
def test_every_declared_category_is_accepted(observations: ObservationStore, category: str) -> None:
    assert add_observation(observations, category=category).category == category


def test_an_unknown_category_is_rejected(observations: ObservationStore) -> None:
    with pytest.raises(ValidationError, match="must be one of"):
        add_observation(observations, category="vibes")


def test_an_unknown_outcome_is_rejected(observations: ObservationStore) -> None:
    with pytest.raises(ValidationError, match="must be one of"):
        add_observation(observations, outcome="probably fine")


def test_an_empty_classification_is_rejected(observations: ObservationStore) -> None:
    with pytest.raises(ValidationError):
        add_observation(observations, classification="   ")


def test_evidence_references_are_validated(observations: ObservationStore) -> None:
    with pytest.raises(ValidationError):
        add_observation(observations, evidence=["nope"])


def test_observations_are_allocated_sequential_identifiers(
    observations: ObservationStore,
) -> None:
    assert [add_observation(observations).id for _ in range(3)] == [
        "OBS-0001",
        "OBS-0002",
        "OBS-0003",
    ]
