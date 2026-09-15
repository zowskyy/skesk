"""Behavioural tests for the hand-written schema engine.

These test the engine's *contract*, not its internals: given a schema and a
document, which issues come back, at which paths. The engine stands in for
jsonschema/pydantic, so the adversarial cases matter more than the happy ones.
"""

from __future__ import annotations

import pytest
from skillkernel.core.errors import ValidationError
from skillkernel.core.schema import (
    Schema,
    any_spec,
    bool_spec,
    enum_spec,
    id_spec,
    int_spec,
    list_spec,
    map_spec,
    number_spec,
    object_spec,
    str_spec,
    timestamp_spec,
)


def make_schema(fields: dict[str, object], **kwargs: object) -> Schema:
    root_fields = {"schema_version": int_spec(required=True), **fields}
    return Schema(
        name="test",
        supported_versions=(1,),
        root=object_spec(root_fields, **kwargs),  # type: ignore[arg-type]
    )


def paths(schema: Schema, document: object) -> list[str]:
    return [issue.path for issue in schema.issues(document)]


def messages(schema: Schema, document: object) -> str:
    return "\n".join(str(issue) for issue in schema.issues(document))


# --- required fields -------------------------------------------------------


def test_required_field_present_is_valid() -> None:
    schema = make_schema({"name": str_spec(required=True)})
    assert schema.is_valid({"schema_version": 1, "name": "ok"})


def test_missing_required_field_is_reported_with_its_path() -> None:
    schema = make_schema({"name": str_spec(required=True)})
    issues = schema.issues({"schema_version": 1})
    assert [issue.path for issue in issues] == ["name"]
    assert issues[0].code == "missing-field"


def test_optional_field_may_be_absent() -> None:
    schema = make_schema({"note": str_spec()})
    assert schema.is_valid({"schema_version": 1})


def test_present_but_null_is_rejected_unless_nullable() -> None:
    schema = make_schema({"name": str_spec(required=True)})
    assert paths(schema, {"schema_version": 1, "name": None}) == ["name"]


def test_nullable_field_accepts_null() -> None:
    schema = make_schema({"name": str_spec(required=True, nullable=True)})
    assert schema.is_valid({"schema_version": 1, "name": None})


# --- scalar types ----------------------------------------------------------


@pytest.mark.parametrize(
    ("spec", "bad_value"),
    [
        (str_spec(required=True), 5),
        (str_spec(required=True), ["a"]),
        (int_spec(required=True), "5"),
        (int_spec(required=True), 1.5),
        (number_spec(required=True), "5"),
        (bool_spec(required=True), "true"),
        (bool_spec(required=True), 1),
    ],
)
def test_wrong_scalar_type_is_rejected(spec: object, bad_value: object) -> None:
    schema = make_schema({"field": spec})
    assert paths(schema, {"schema_version": 1, "field": bad_value}) == ["field"]


def test_bool_is_not_accepted_as_an_integer() -> None:
    """``bool`` subclasses ``int`` in Python; a stray ``true`` must not pass as 1."""
    schema = make_schema({"count": int_spec(required=True)})
    assert not schema.is_valid({"schema_version": 1, "count": True})
    assert schema.is_valid({"schema_version": 1, "count": 1})


def test_bool_is_not_accepted_as_a_number() -> None:
    schema = make_schema({"ratio": number_spec(required=True)})
    assert not schema.is_valid({"schema_version": 1, "ratio": False})


def test_integer_is_accepted_as_a_number() -> None:
    schema = make_schema({"ratio": number_spec(required=True)})
    assert schema.is_valid({"schema_version": 1, "ratio": 3})


def test_string_min_length_ignores_surrounding_whitespace() -> None:
    schema = make_schema({"name": str_spec(required=True, min_length=1)})
    assert not schema.is_valid({"schema_version": 1, "name": "   "})
    assert schema.is_valid({"schema_version": 1, "name": " x "})


@pytest.mark.parametrize("value", [-1, 11])
def test_numeric_bounds_are_enforced(value: int) -> None:
    schema = make_schema({"n": int_spec(required=True, minimum=0, maximum=10)})
    assert paths(schema, {"schema_version": 1, "n": value}) == ["n"]


def test_numeric_bounds_are_inclusive() -> None:
    schema = make_schema({"n": int_spec(required=True, minimum=0, maximum=10)})
    assert schema.is_valid({"schema_version": 1, "n": 0})
    assert schema.is_valid({"schema_version": 1, "n": 10})


# --- enum, id, timestamp ---------------------------------------------------


def test_enum_rejects_values_outside_the_set_and_lists_them() -> None:
    schema = make_schema({"status": enum_spec(("a", "b"), required=True)})
    assert "must be one of a, b" in messages(schema, {"schema_version": 1, "status": "c"})


def test_enum_rejects_a_non_string() -> None:
    schema = make_schema({"status": enum_spec(("a",), required=True)})
    assert not schema.is_valid({"schema_version": 1, "status": 1})


@pytest.mark.parametrize("value", ["K-0001", "K-00001"])
def test_id_spec_accepts_well_formed_identifiers(value: str) -> None:
    schema = make_schema({"ref": id_spec("K", required=True)})
    assert schema.is_valid({"schema_version": 1, "ref": value})


@pytest.mark.parametrize("value", ["K-1", "EXP-0001", "K0001", "k-0001", "K-0000", 1, None])
def test_id_spec_rejects_malformed_or_foreign_identifiers(value: object) -> None:
    schema = make_schema({"ref": id_spec("K", required=True)})
    assert not schema.is_valid({"schema_version": 1, "ref": value})


@pytest.mark.parametrize(
    "value",
    [
        "2024-01-31T12:00:00",  # no zone
        "2024-01-31 12:00:00Z",  # no T
        "2024-13-01T00:00:00Z",  # month 13
        "2024-02-30T00:00:00Z",  # impossible day
        "not-a-time",
        12345,
    ],
)
def test_timestamp_spec_rejects_bad_timestamps(value: object) -> None:
    schema = make_schema({"at": timestamp_spec(required=True)})
    assert not schema.is_valid({"schema_version": 1, "at": value})


def test_timestamp_spec_accepts_a_utc_timestamp() -> None:
    schema = make_schema({"at": timestamp_spec(required=True)})
    assert schema.is_valid({"schema_version": 1, "at": "2024-01-31T12:00:00Z"})


# --- collections -----------------------------------------------------------


def test_list_rejects_a_non_list() -> None:
    schema = make_schema({"items": list_spec(str_spec(), required=True)})
    assert paths(schema, {"schema_version": 1, "items": {"a": 1}}) == ["items"]


def test_list_items_are_validated_with_indexed_paths() -> None:
    schema = make_schema({"items": list_spec(str_spec(), required=True)})
    assert paths(schema, {"schema_version": 1, "items": ["ok", 5, "ok", 7]}) == [
        "items[1]",
        "items[3]",
    ]


def test_empty_list_is_valid_unless_min_items_is_set() -> None:
    permissive = make_schema({"items": list_spec(str_spec(), required=True)})
    assert permissive.is_valid({"schema_version": 1, "items": []})
    strict = make_schema({"items": list_spec(str_spec(), required=True, min_items=1)})
    assert not strict.is_valid({"schema_version": 1, "items": []})


def test_unique_list_rejects_duplicates() -> None:
    schema = make_schema({"items": list_spec(str_spec(), required=True, unique=True)})
    assert not schema.is_valid({"schema_version": 1, "items": ["a", "a"]})
    assert schema.is_valid({"schema_version": 1, "items": ["a", "b"]})


def test_map_rejects_a_non_mapping_and_validates_its_values() -> None:
    schema = make_schema({"env": map_spec(str_spec(), required=True)})
    assert paths(schema, {"schema_version": 1, "env": ["a"]}) == ["env"]
    assert paths(schema, {"schema_version": 1, "env": {"os": 5}}) == ["env.os"]


def test_empty_map_and_empty_object_are_valid() -> None:
    schema = make_schema(
        {"env": map_spec(str_spec(), required=True), "block": object_spec({}, required=True)}
    )
    assert schema.is_valid({"schema_version": 1, "env": {}, "block": {}})


def test_any_spec_accepts_anything_including_null() -> None:
    schema = make_schema({"blob": any_spec(required=True)})
    for value in [None, 1, "s", [1, 2], {"a": {"b": 1}}, True]:
        assert schema.is_valid({"schema_version": 1, "blob": value})


# --- nesting ---------------------------------------------------------------


def test_nested_objects_report_dotted_paths() -> None:
    schema = make_schema(
        {
            "outer": object_spec(
                {"inner": object_spec({"leaf": int_spec(required=True)}, required=True)},
                required=True,
            )
        }
    )
    document = {"schema_version": 1, "outer": {"inner": {"leaf": "nope"}}}
    assert paths(schema, document) == ["outer.inner.leaf"]


def test_nested_missing_field_reports_the_full_path() -> None:
    schema = make_schema({"outer": object_spec({"leaf": str_spec(required=True)}, required=True)})
    assert paths(schema, {"schema_version": 1, "outer": {}}) == ["outer.leaf"]


def test_objects_inside_lists_report_combined_paths() -> None:
    schema = make_schema(
        {"rows": list_spec(object_spec({"name": str_spec(required=True)}), required=True)}
    )
    document = {"schema_version": 1, "rows": [{"name": "ok"}, {}]}
    assert paths(schema, document) == ["rows[1].name"]


def test_object_field_given_a_scalar_is_rejected() -> None:
    schema = make_schema({"outer": object_spec({"leaf": str_spec()}, required=True)})
    assert paths(schema, {"schema_version": 1, "outer": "scalar"}) == ["outer"]


# --- unknown fields --------------------------------------------------------


def test_unknown_field_is_rejected_by_default() -> None:
    schema = make_schema({"name": str_spec(required=True)})
    issues = schema.issues({"schema_version": 1, "name": "x", "surprise": 1})
    assert [issue.path for issue in issues] == ["surprise"]
    assert issues[0].code == "unknown-field"


def test_extension_prefixed_fields_are_allowed_in_extension_mode() -> None:
    schema = make_schema({"name": str_spec(required=True)}, unknown="allow_extension")
    assert schema.is_valid({"schema_version": 1, "name": "x", "x_vendor": {"any": "thing"}})


def test_non_prefixed_unknown_field_is_still_rejected_in_extension_mode() -> None:
    schema = make_schema({"name": str_spec(required=True)}, unknown="allow_extension")
    issues = schema.issues({"schema_version": 1, "name": "x", "vendor": 1})
    assert [issue.path for issue in issues] == ["vendor"]
    assert "x_" in issues[0].message


def test_allow_mode_accepts_any_field() -> None:
    schema = make_schema({"name": str_spec(required=True)}, unknown="allow")
    assert schema.is_valid({"schema_version": 1, "name": "x", "whatever": [1]})


def test_unknown_field_policy_applies_to_nested_objects_independently() -> None:
    schema = make_schema(
        {
            "strict": object_spec({"a": str_spec()}, required=True),
            "loose": object_spec({"a": str_spec()}, required=True, unknown="allow_extension"),
        },
        unknown="allow_extension",
    )
    document = {
        "schema_version": 1,
        "strict": {"a": "x", "x_ext": 1},
        "loose": {"a": "x", "x_ext": 1},
    }
    assert paths(schema, document) == ["strict.x_ext"]


def test_allow_mode_still_validates_declared_fields() -> None:
    schema = make_schema({"name": str_spec(required=True)}, unknown="allow")
    assert paths(schema, {"schema_version": 1, "name": 5, "extra": 1}) == ["name"]


# --- schema versioning -----------------------------------------------------


def test_missing_schema_version_is_reported() -> None:
    schema = make_schema({})
    issues = schema.issues({})
    assert any(issue.path == "schema_version" and issue.code == "missing-field" for issue in issues)


def test_unsupported_schema_version_is_reported_with_the_supported_set() -> None:
    schema = Schema(name="t", supported_versions=(1, 2), root=object_spec({}, unknown="allow"))
    issues = schema.issues({"schema_version": 3})
    assert issues[0].code == "unsupported-version"
    assert "supported: 1, 2" in issues[0].message


def test_non_integer_schema_version_is_reported() -> None:
    schema = make_schema({})
    assert any(issue.path == "schema_version" for issue in schema.issues({"schema_version": "1"}))


def test_boolean_schema_version_is_rejected() -> None:
    schema = make_schema({})
    assert any(issue.path == "schema_version" for issue in schema.issues({"schema_version": True}))


# --- root and aggregation --------------------------------------------------


@pytest.mark.parametrize("document", ["a string", ["a", "list"], 5, None])
def test_non_mapping_root_is_rejected(document: object) -> None:
    schema = make_schema({})
    issues = schema.issues(document)
    assert len(issues) == 1
    assert issues[0].path == ""


def test_all_violations_are_reported_together_not_just_the_first() -> None:
    schema = make_schema(
        {
            "a": str_spec(required=True),
            "b": int_spec(required=True),
            "c": list_spec(str_spec(), required=True),
        }
    )
    document = {"schema_version": 99, "a": 1, "c": ["ok", 2], "extra": True}
    reported = paths(schema, document)
    assert set(reported) == {"schema_version", "a", "b", "c[1]", "extra"}


def test_validate_raises_with_every_issue_and_the_source_name() -> None:
    schema = make_schema({"a": str_spec(required=True)})
    with pytest.raises(ValidationError) as excinfo:
        schema.validate({"schema_version": 1}, source="thing.yaml")
    assert "thing.yaml" in str(excinfo.value)
    assert excinfo.value.issues == ["a: is required but missing"]


def test_validate_returns_the_document_when_valid() -> None:
    schema = make_schema({"a": str_spec(required=True)})
    document = {"schema_version": 1, "a": "x"}
    assert schema.validate(document) is document


def test_non_string_mapping_keys_are_rejected() -> None:
    schema = make_schema({}, unknown="allow")
    issues = schema.issues({"schema_version": 1, 5: "x"})
    assert any("keys must be strings" in str(issue) for issue in issues)


def test_malformed_spec_kind_fails_loudly_rather_than_passing_silently() -> None:
    """A schema built with a bad spec must not quietly validate everything."""
    from skillkernel.core.schema import Spec

    schema = Schema(
        name="broken",
        supported_versions=(1,),
        root=object_spec({"field": Spec(kind="nonsense", required=True)}),
    )
    with pytest.raises(AssertionError):
        schema.issues({"schema_version": 1, "field": "x"})


def test_issue_str_names_the_root_when_the_path_is_empty() -> None:
    schema = make_schema({})
    assert str(schema.issues("not a mapping")[0]).startswith("<root>:")
