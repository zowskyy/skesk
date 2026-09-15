"""Identifiers, timestamps, text normalization, YAML I/O and layout."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from skillkernel.core import ids
from skillkernel.core.clock import NOW_ENV_VAR, is_timestamp, now_iso
from skillkernel.core.errors import (
    NotInitializedError,
    UnsafeOperationError,
    ValidationError,
)
from skillkernel.core.paths import CONFIG_FILENAME, Layout, find_root
from skillkernel.core.yamlio import dump_yaml, load_yaml_file, write_yaml_file
from skillkernel.utils.text import normalize_key, slugify

# --- identifiers -----------------------------------------------------------


def test_identifiers_are_zero_padded_to_four_digits() -> None:
    assert ids.format_id("SKILL", 1) == "SKILL-0001"
    assert ids.format_id("K", 42) == "K-0042"


def test_identifiers_grow_beyond_four_digits_without_truncating() -> None:
    assert ids.format_id("EV", 123456) == "EV-123456"
    assert ids.parse_id("EV-123456").sequence == 123456


def test_parse_round_trips_format() -> None:
    parsed = ids.parse_id("EXP-0007", "EXP")
    assert (parsed.prefix, parsed.sequence) == ("EXP", 7)
    assert str(parsed) == "EXP-0007"


@pytest.mark.parametrize("value", ["K-1", "K0001", "k-0001", "K-", "-0001", "", "K-0001x"])
def test_malformed_identifiers_are_rejected(value: str) -> None:
    with pytest.raises(ValidationError):
        ids.parse_id(value)


def test_sequence_zero_is_not_a_valid_identifier() -> None:
    assert not ids.is_valid_id("K-0000")
    with pytest.raises(ValueError, match="must be >= 1"):
        ids.format_id("K", 0)


def test_prefix_mismatch_is_rejected() -> None:
    with pytest.raises(ValidationError, match="expected a K-"):
        ids.parse_id("EXP-0001", "K")


def test_non_string_identifiers_are_rejected_without_raising() -> None:
    assert not ids.is_valid_id(None)
    assert not ids.is_valid_id(7)
    with pytest.raises(ValidationError):
        ids.parse_id(7)  # type: ignore[arg-type]


# --- clock -----------------------------------------------------------------


def test_now_iso_produces_a_valid_kernel_timestamp() -> None:
    assert is_timestamp(now_iso())


def test_now_iso_honours_a_pinned_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(NOW_ENV_VAR, "2020-06-01T00:00:00Z")
    assert now_iso() == "2020-06-01T00:00:00Z"


def test_an_invalid_override_is_an_error_not_a_silent_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(NOW_ENV_VAR, "yesterday")
    with pytest.raises(ValueError, match=NOW_ENV_VAR):
        now_iso()


@pytest.mark.parametrize(
    "value", ["2024-02-30T00:00:00Z", "2024-13-01T00:00:00Z", "2024-01-31T25:00:00Z"]
)
def test_is_timestamp_rejects_impossible_dates(value: str) -> None:
    assert not is_timestamp(value)


# --- text normalization ----------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Flaky Import Order", "flaky import order"),
        ("  flaky   import\torder  ", "flaky import order"),
        ("FLAKY IMPORT ORDER", "flaky import order"),
    ],
)
def test_normalize_key_collapses_case_and_whitespace(raw: str, expected: str) -> None:
    assert normalize_key(raw) == expected


def test_normalize_key_does_not_collapse_genuinely_different_labels() -> None:
    assert normalize_key("import order") != normalize_key("import-order")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("Deterministic Frame Rendering", "deterministic-frame-rendering"), ("a/b__c", "a-b-c")],
)
def test_slugify(raw: str, expected: str) -> None:
    assert slugify(raw) == expected


def test_slugify_refuses_input_with_no_slug_characters() -> None:
    with pytest.raises(ValueError, match="cannot derive a slug"):
        slugify("///")


# --- YAML I/O --------------------------------------------------------------


def test_dump_preserves_key_insertion_order_for_reviewable_diffs() -> None:
    text = dump_yaml({"zebra": 1, "apple": 2, "mango": 3})
    assert text.index("zebra") < text.index("apple") < text.index("mango")


def test_yaml_round_trips_through_a_file(tmp_path: Path) -> None:
    payload = {"a": 1, "b": ["x", "y"], "c": {"d": None}, "e": True}
    target = tmp_path / "nested" / "doc.yaml"
    write_yaml_file(target, payload)
    assert load_yaml_file(target) == payload


def test_write_yaml_file_prepends_a_header_comment(tmp_path: Path) -> None:
    target = tmp_path / "doc.yaml"
    write_yaml_file(target, {"a": 1}, header="# hello\n")
    text = target.read_text()
    assert text.startswith("# hello\n")
    assert load_yaml_file(target) == {"a": 1}


def test_multiline_strings_use_block_style_and_survive_a_round_trip(tmp_path: Path) -> None:
    target = tmp_path / "doc.yaml"
    write_yaml_file(target, {"body": "line one\nline two"})
    assert "|-" in target.read_text()
    assert load_yaml_file(target) == {"body": "line one\nline two"}


def test_missing_file_raises_a_validation_error(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="does not exist"):
        load_yaml_file(tmp_path / "absent.yaml")


def test_empty_file_raises_rather_than_returning_none(tmp_path: Path) -> None:
    target = tmp_path / "empty.yaml"
    target.write_text("")
    with pytest.raises(ValidationError, match="is empty"):
        load_yaml_file(target)


def test_malformed_yaml_raises_a_validation_error(tmp_path: Path) -> None:
    target = tmp_path / "bad.yaml"
    target.write_text("a: [1, 2\nb: {")
    with pytest.raises(ValidationError, match="not valid YAML"):
        load_yaml_file(target)


def test_invalid_utf8_raises_a_validation_error(tmp_path: Path) -> None:
    target = tmp_path / "bad.yaml"
    target.write_bytes(b"a: \xff\xfe\n")
    with pytest.raises(ValidationError, match="not valid UTF-8"):
        load_yaml_file(target)


def test_loading_uses_safe_load_and_will_not_construct_python_objects(tmp_path: Path) -> None:
    """A record is data. It must never be able to instantiate a Python object."""
    target = tmp_path / "evil.yaml"
    target.write_text("!!python/object/apply:os.system ['echo pwned']\n")
    with pytest.raises(ValidationError, match="not valid YAML"):
        load_yaml_file(target)


# --- layout ----------------------------------------------------------------


def test_find_root_locates_the_nearest_marker_file_walking_upwards(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILENAME).write_text("schema_version: 1\n")
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    assert find_root(deep) == tmp_path


def test_find_root_raises_when_the_repository_is_not_initialized(tmp_path: Path) -> None:
    with pytest.raises(NotInitializedError, match="skillkernel init"):
        find_root(tmp_path)


def test_find_root_prefers_the_nearest_marker(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILENAME).write_text("x")
    inner = tmp_path / "inner"
    inner.mkdir()
    (inner / CONFIG_FILENAME).write_text("x")
    assert find_root(inner / "deeper" if (inner / "deeper").exists() else inner) == inner


def test_every_managed_directory_is_inside_the_root(tmp_path: Path) -> None:
    built = Layout(root=tmp_path)
    for directory in built.managed_directories():
        assert tmp_path in directory.parents or directory == tmp_path


def test_require_inside_rejects_paths_that_escape_the_repository(tmp_path: Path) -> None:
    built = Layout(root=tmp_path / "repo")
    built.root.mkdir()
    with pytest.raises(UnsafeOperationError, match="outside the SkillKernel repository"):
        built.require_inside(built.root / ".." / "elsewhere")


def test_require_inside_accepts_a_path_within_the_repository(tmp_path: Path) -> None:
    built = Layout(root=tmp_path)
    assert built.require_inside(tmp_path / "skills" / "core") == (tmp_path / "skills" / "core")


def test_relative_renders_posix_paths_regardless_of_platform(tmp_path: Path) -> None:
    built = Layout(root=tmp_path)
    assert built.relative(tmp_path / "a" / "b.yaml") == "a/b.yaml"


def test_unknown_skill_scope_is_rejected(tmp_path: Path) -> None:
    built = Layout(root=tmp_path)
    with pytest.raises(ValueError, match="unknown skill scope"):
        built.skill_scope_dir("deprecated")


def test_managed_directories_are_unique(tmp_path: Path) -> None:
    directories = Layout(root=tmp_path).managed_directories()
    assert len(set(directories)) == len(directories)


def test_layout_does_not_depend_on_the_process_working_directory(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILENAME).write_text("x")
    original = Path.cwd()
    try:
        os.chdir(tmp_path)
        assert find_root() == tmp_path.resolve()
    finally:
        os.chdir(original)
