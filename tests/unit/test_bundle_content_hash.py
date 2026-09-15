"""The frozen content-hash contract.

Written deliberately *before* the first real bundle exists, so the hashing rules
are a decision rather than an accident of whatever the first asset happened to
contain. Once these vectors pass they are part of the portability contract:
changing them changes what "the same portable content" means.

The algorithm: for each portable file, take its normalized POSIX relative path
and its *parsed* content, serialize the content through the existing
``canonical_json``, frame both with fixed-width big-endian lengths, order by
path, and SHA-256 the framed sequence behind a domain separator.

Hashing parsed content is what removes serialization noise. Fixed-width length
prefixes are what make the framing structurally unambiguous — no delimiter can
be forged inside a path or a payload.
"""

from __future__ import annotations

from typing import Any

import pytest
import yaml
from skillkernel.bundles.model import (
    MANIFEST_FILENAME,
    SELF_EXCLUDED_MANIFEST_KEY,
    content_hash,
)
from skillkernel.core.errors import ValidationError


def parse(text: str) -> Any:
    return yaml.safe_load(text)


BASE = {
    "bundle.yaml": {"bundle_id": "b", "name": "N", "items": ["one", "two"]},
    "examples/positive/p1.yaml": {"case_id": "p1", "signals": ["t"]},
}


# --- serialization noise must not change the hash --------------------------


def test_yaml_key_ordering_does_not_change_the_hash() -> None:
    a = {"m.yaml": parse("a: 1\nb: 2\nc: 3")}
    b = {"m.yaml": parse("c: 3\nb: 2\na: 1")}
    assert content_hash(a) == content_hash(b)


def test_insignificant_whitespace_does_not_change_the_hash() -> None:
    a = {"m.yaml": parse("a: 1\nb:\n  - x\n  - y\n")}
    b = {"m.yaml": parse("a:    1\nb: [x, y]\n")}
    assert content_hash(a) == content_hash(b)


def test_crlf_and_lf_produce_the_same_hash() -> None:
    """Line endings differ between checkouts; portable content does not."""
    lf = {"m.yaml": parse("a: 1\nb: |\n  line one\n  line two\n")}
    crlf = {"m.yaml": parse("a: 1\r\nb: |\r\n  line one\r\n  line two\r\n")}
    assert content_hash(lf) == content_hash(crlf)


def test_file_enumeration_order_does_not_change_the_hash() -> None:
    forward = dict(BASE)
    reverse = dict(reversed(list(BASE.items())))
    assert list(forward) != list(reverse)
    assert content_hash(forward) == content_hash(reverse)


def test_the_hash_is_independent_of_any_directory() -> None:
    """Nothing about where the bundle lives participates."""
    assert content_hash(BASE) == content_hash(dict(BASE))


def test_backslash_paths_normalize_to_posix() -> None:
    posix = {"examples/positive/p1.yaml": {"case_id": "p1"}}
    windows = {"examples\\positive\\p1.yaml": {"case_id": "p1"}}
    assert content_hash(posix) == content_hash(windows)


def test_repeated_separators_normalize() -> None:
    assert content_hash({"a//b.yaml": {"x": 1}}) == content_hash({"a/b.yaml": {"x": 1}})


# --- semantic differences must change the hash -----------------------------


def test_changing_a_scalar_changes_the_hash() -> None:
    changed = {**BASE, "bundle.yaml": {**BASE["bundle.yaml"], "name": "DIFFERENT"}}
    assert content_hash(changed) != content_hash(BASE)


def test_changing_list_order_changes_the_hash() -> None:
    """List order is semantically meaningful: a procedure is a sequence."""
    changed = {**BASE, "bundle.yaml": {**BASE["bundle.yaml"], "items": ["two", "one"]}}
    assert content_hash(changed) != content_hash(BASE)


def test_adding_a_file_changes_the_hash() -> None:
    added = {**BASE, "examples/negative/n1.yaml": {"case_id": "n1", "signals": ["s"]}}
    assert content_hash(added) != content_hash(BASE)


def test_removing_a_file_changes_the_hash() -> None:
    removed = {"bundle.yaml": BASE["bundle.yaml"]}
    assert content_hash(removed) != content_hash(BASE)


def test_renaming_a_file_changes_the_hash() -> None:
    renamed = {
        "bundle.yaml": BASE["bundle.yaml"],
        "examples/negative/p1.yaml": BASE["examples/positive/p1.yaml"],
    }
    assert content_hash(renamed) != content_hash(BASE)


def test_moving_content_between_files_changes_the_hash() -> None:
    """The framing binds each payload to its path."""
    a = {"one.yaml": {"x": 1}, "two.yaml": {"y": 2}}
    b = {"one.yaml": {"y": 2}, "two.yaml": {"x": 1}}
    assert content_hash(a) != content_hash(b)


def test_the_framing_is_not_delimiter_forgeable() -> None:
    """A path containing separators cannot impersonate two files."""
    a = {"ab.yaml": {"x": 1}}
    b = {"a.yaml": {"x": 1}, "b.yaml": {"x": 1}}
    assert content_hash(a) != content_hash(b)


def test_an_empty_file_set_is_distinct_from_any_content() -> None:
    assert content_hash({}) != content_hash({"a.yaml": {}})


# --- self-exclusion --------------------------------------------------------


def test_the_manifests_own_hash_field_is_excluded() -> None:
    """Otherwise the manifest would have to contain its own digest."""
    without = {MANIFEST_FILENAME: {"bundle_id": "b"}}
    with_declared = {
        MANIFEST_FILENAME: {"bundle_id": "b", SELF_EXCLUDED_MANIFEST_KEY: "sha256:anything"}
    }
    assert content_hash(without) == content_hash(with_declared)


def test_the_exclusion_applies_only_to_the_manifest() -> None:
    """A field of the same name elsewhere is ordinary content."""
    a = {"other.yaml": {"content_hash": "x"}}
    b = {"other.yaml": {"content_hash": "y"}}
    assert content_hash(a) != content_hash(b)


def test_changing_any_other_manifest_field_still_changes_the_hash() -> None:
    a = {MANIFEST_FILENAME: {"bundle_id": "b", SELF_EXCLUDED_MANIFEST_KEY: "sha256:z"}}
    b = {MANIFEST_FILENAME: {"bundle_id": "c", SELF_EXCLUDED_MANIFEST_KEY: "sha256:z"}}
    assert content_hash(a) != content_hash(b)


# --- ambiguity is refused, never silently resolved -------------------------


def test_paths_colliding_after_normalization_are_rejected() -> None:
    with pytest.raises(ValidationError, match="normalize to the same path"):
        content_hash({"a/b.yaml": {"x": 1}, "a\\b.yaml": {"x": 2}})


@pytest.mark.parametrize("path", ["/absolute.yaml", "../escape.yaml", "a/../b.yaml", "./a.yaml"])
def test_non_relative_paths_are_rejected(path: str) -> None:
    with pytest.raises(ValidationError, match="not a plain relative path"):
        content_hash({path: {"x": 1}})


def test_an_empty_path_is_rejected() -> None:
    with pytest.raises(ValidationError, match="empty path"):
        content_hash({"   ": {"x": 1}})


# --- stability -------------------------------------------------------------


def test_the_hash_is_stable_across_calls() -> None:
    assert content_hash(BASE) == content_hash(BASE)


def test_the_hash_is_prefixed_with_its_algorithm() -> None:
    digest = content_hash(BASE)
    assert digest.startswith("sha256:")
    assert len(digest.split(":")[1]) == 64


FROZEN_VECTOR = "sha256:414640dcd9cf51bd2358491fffeecce7a080dfea23a1e44746d6403e4f646533"
"""A pinned digest for a fixed two-file bundle.

If this value ever changes, the framing or the canonicalization changed, and
every previously recorded ``content_hash`` became meaningless. That is a
contract change, not a refactor.
"""


def test_a_known_vector_is_frozen() -> None:
    vector = {
        "bundle.yaml": {"bundle_id": "vector", "bundle_version": "1.0.0"},
        "examples/positive/p1.yaml": {"case_id": "p1", "signals": ["alpha"]},
    }
    assert content_hash(vector) == FROZEN_VECTOR


def test_schema_validation_is_what_makes_canonical_json_safe_here() -> None:
    """The one boundary of the hash, made explicit rather than assumed.

    ``canonical_json`` serializes an unexpected type via ``str()``, so an
    unquoted YAML date and the equivalent quoted string canonicalize
    identically. That collision is unreachable for a valid bundle because the
    bundle schema types every field, and a date where a string is required is
    rejected before hashing. This test records both halves so the reliance is
    visible.
    """
    import datetime

    from skillkernel.bundles.model import BUNDLE_SCHEMA

    # The collision exists in the serializer...
    assert content_hash({"a.yaml": {"v": datetime.date(2024, 1, 1)}}) == content_hash(
        {"a.yaml": {"v": "2024-01-01"}}
    )

    # ...and the schema is what prevents a bundle from reaching it.
    issues = BUNDLE_SCHEMA.issues(
        {"schema_version": 1, "bundle_version": datetime.date(2024, 1, 1)}
    )
    assert any(issue.path == "bundle_version" for issue in issues)
