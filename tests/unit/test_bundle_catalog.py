"""Reading a bundle, and refusing the ones that are not what they claim.

A bundle is external input. Everything here is about the difference between
*parsing* it and *trusting* it: the catalog parses, validates against the
bundle schema, checks the declared integrity claim against the shipped bytes,
and refuses anything that carries state a receiving workspace is supposed to
earn for itself.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from skillkernel.bundles.catalog import available_bundles, load_bundle, packaged_root
from skillkernel.bundles.model import content_hash
from skillkernel.core.errors import RecordNotFoundError, ValidationError

Build = Callable[..., Path]
Manifest = dict[str, Any]
Cases = list[dict[str, Any]]


# --- the packaged bundle ---------------------------------------------------


def test_the_packaged_bundle_is_discoverable() -> None:
    assert "two-method-escalation" in available_bundles()


def test_the_packaged_bundle_loads_and_its_declared_hash_is_correct() -> None:
    bundle = load_bundle("two-method-escalation")
    assert bundle.manifest["content_hash"] == bundle.content_hash, (
        "the shipped manifest declares a content hash that does not match the "
        "shipped content; the asset and its integrity claim have drifted apart"
    )


def test_the_packaged_root_resolves_through_importlib_resources() -> None:
    """Never ``__file__``: that is what works in a checkout and fails in a wheel."""
    assert packaged_root().is_dir()


def test_an_unknown_bundle_names_what_is_available() -> None:
    with pytest.raises(RecordNotFoundError) as exc:
        load_bundle("no-such-bundle")
    assert "two-method-escalation" in str(exc.value)


# --- shape -----------------------------------------------------------------


def test_a_valid_synthetic_bundle_loads(make_bundle: Build) -> None:
    bundle = load_bundle("demo-skill", root=make_bundle())
    assert bundle.slug == "demo-skill"
    assert bundle.scope == "core"
    assert len(bundle.positive_cases) == 1
    assert len(bundle.negative_cases) == 1


def test_a_bundle_computes_its_hash_even_when_it_declares_none(make_bundle: Build) -> None:
    bundle = load_bundle("demo-skill", root=make_bundle())
    assert bundle.content_hash.startswith("sha256:")
    assert bundle.manifest["content_hash"] is None


def declared_hash(manifest: Manifest, positive: Cases, negative: Cases) -> str:
    documents: dict[str, Any] = {"bundle.yaml": manifest}
    for case in positive:
        documents[f"examples/positive/{case['case_id']}.yaml"] = case
    for case in negative:
        documents[f"examples/negative/{case['case_id']}.yaml"] = case
    return content_hash(documents)


def test_a_declared_hash_that_matches_is_accepted(
    make_bundle: Build, bundle_manifest: Manifest, bundle_positive: Cases, bundle_negative: Cases
) -> None:
    bundle_manifest["content_hash"] = declared_hash(
        bundle_manifest, bundle_positive, bundle_negative
    )
    root = make_bundle(manifest=bundle_manifest)
    assert load_bundle("demo-skill", root=root).content_hash == bundle_manifest["content_hash"]


def test_a_declared_hash_that_does_not_match_is_refused(
    make_bundle: Build, bundle_manifest: Manifest
) -> None:
    bundle_manifest["content_hash"] = "sha256:" + "0" * 64
    root = make_bundle(manifest=bundle_manifest)
    with pytest.raises(ValidationError, match="do not match"):
        load_bundle("demo-skill", root=root)


def test_a_tampered_case_changes_the_hash_and_breaks_the_claim(
    make_bundle: Build, bundle_manifest: Manifest, bundle_positive: Cases, bundle_negative: Cases
) -> None:
    """The integrity claim covers every portable file, not just the manifest."""
    bundle_manifest["content_hash"] = declared_hash(
        bundle_manifest, bundle_positive, bundle_negative
    )
    tampered = [{**bundle_positive[0], "signals": ["demo", "smuggled"]}]
    root = make_bundle(manifest=bundle_manifest, positive=tampered)
    with pytest.raises(ValidationError, match="do not match"):
        load_bundle("demo-skill", root=root)


# --- refusals --------------------------------------------------------------


def test_an_unsupported_schema_version_is_refused(
    make_bundle: Build, bundle_manifest: Manifest
) -> None:
    bundle_manifest["schema_version"] = 99
    root = make_bundle(manifest=bundle_manifest)
    with pytest.raises(ValidationError, match="not supported"):
        load_bundle("demo-skill", root=root)


def test_corrupt_yaml_is_refused(make_bundle: Build) -> None:
    root = make_bundle(manifest_text="bundle_id: [unclosed\n")
    with pytest.raises(ValidationError):
        load_bundle("demo-skill", root=root)


def test_an_empty_manifest_is_refused(make_bundle: Build) -> None:
    root = make_bundle(manifest_text="")
    with pytest.raises(ValidationError, match="empty"):
        load_bundle("demo-skill", root=root)


def test_an_unknown_manifest_key_is_refused(make_bundle: Build, bundle_manifest: Manifest) -> None:
    """A bundle is external input: an unknown key is a mistake, not an extension."""
    bundle_manifest["x_smuggled"] = "anything"
    root = make_bundle(manifest=bundle_manifest)
    with pytest.raises(ValidationError):
        load_bundle("demo-skill", root=root)


@pytest.mark.parametrize(
    "field, value",
    [
        ("id", "SKILL-0001"),
        ("maturity", "trusted"),
        ("confidence", "high"),
        ("evidence", {"experiments": ["EXP-0001"], "knowledge": [], "records": []}),
        ("provenance", {"created_by": "manual"}),
        ("history", [{"to": "trusted"}]),
        ("classification", {"scope": "core", "maturity": "validated"}),
        ("created_at", "2024-01-01T00:00:00Z"),
        ("version", "9.9.9"),
        ("project_scope", {"origin_project": "somewhere-else"}),
        ("deprecation", None),
        ("updated_at", "2024-01-01T00:00:00Z"),
    ],
)
def test_a_bundle_may_not_carry_workspace_owned_state(
    make_bundle: Build, bundle_manifest: Manifest, field: str, value: Any
) -> None:
    """The whole point of the format: a bundle ships a definition, never a record."""
    bundle_manifest[field] = value
    root = make_bundle(manifest=bundle_manifest)
    with pytest.raises(ValidationError, match="workspace-owned"):
        load_bundle("demo-skill", root=root)


def test_a_directory_name_that_disagrees_with_the_manifest_is_refused(make_bundle: Build) -> None:
    root = make_bundle(directory_name="something-else")
    with pytest.raises(ValidationError, match="bundle_id"):
        load_bundle("something-else", root=root)


def test_a_non_canonical_slug_is_refused(make_bundle: Build, bundle_manifest: Manifest) -> None:
    bundle_manifest["slug"] = "../escape"
    root = make_bundle(manifest=bundle_manifest)
    with pytest.raises(ValidationError, match="canonical slug"):
        load_bundle("demo-skill", root=root)


def test_a_bundle_without_positive_cases_is_refused(make_bundle: Build) -> None:
    root = make_bundle(positive=[])
    with pytest.raises(ValidationError, match="no positive"):
        load_bundle("demo-skill", root=root)


def test_a_bundle_without_negative_cases_is_refused(make_bundle: Build) -> None:
    """Without a negative case the suite cannot measure false activation."""
    root = make_bundle(negative=[])
    with pytest.raises(ValidationError, match="false activation"):
        load_bundle("demo-skill", root=root)


def test_a_case_may_not_declare_its_own_polarity(
    make_bundle: Build, bundle_positive: Cases
) -> None:
    """Polarity comes from the folder, so a case can never disagree with it."""
    root = make_bundle(positive=[{**bundle_positive[0], "expected": "does_not_apply"}])
    with pytest.raises(ValidationError):
        load_bundle("demo-skill", root=root)


def test_a_missing_manifest_is_refused(make_bundle: Build) -> None:
    root = make_bundle()
    (root / "demo-skill" / "bundle.yaml").unlink()
    with pytest.raises(ValidationError, match=r"bundle\.yaml"):
        load_bundle("demo-skill", root=root)


def test_an_absent_bundle_root_reports_that_nothing_ships(tmp_path: Path) -> None:
    with pytest.raises(RecordNotFoundError, match="ships no bundles"):
        load_bundle("demo-skill", root=tmp_path / "does-not-exist")
