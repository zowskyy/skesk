"""The project profile: what this repository *is*.

The profile is intentionally generic. ``domains``, ``objectives`` and
``project.type`` are free lists of strings, because the whole point of the
kernel is that it does not know in advance whether it is serving a renderer, a
compiler or a payroll system. The only structure imposed is the shape that lets
the kernel scope skills, generate documentation and report status.

Verification gates are a free-form mapping so a project can declare gates the
kernel has never heard of (``fuzz: optional``) without a schema change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from skillkernel.core.clock import now_iso
from skillkernel.core.errors import ValidationError
from skillkernel.core.paths import Layout
from skillkernel.core.schema import (
    Schema,
    any_spec,
    enum_spec,
    int_spec,
    list_spec,
    map_spec,
    object_spec,
    str_spec,
    timestamp_spec,
)
from skillkernel.core.yamlio import load_yaml_file, write_yaml_file
from skillkernel.utils.atomic import atomic_write_text

__all__ = [
    "PROFILE_SCHEMA",
    "ProjectProfile",
    "default_profile_document",
    "load_profile",
    "render_profile_markdown",
    "set_profile_values",
    "write_profile",
    "write_profile_markdown",
]

PROFILE_SCHEMA_VERSION = 1

VERIFICATION_LEVELS = ("required", "optional", "not_applicable")

PROFILE_SCHEMA = Schema(
    name="project-profile",
    supported_versions=(PROFILE_SCHEMA_VERSION,),
    root=object_spec(
        {
            "schema_version": int_spec(required=True),
            "project": object_spec(
                {
                    "name": str_spec(required=True, min_length=1),
                    "type": list_spec(str_spec(min_length=1), required=True),
                    "description": str_spec(nullable=True),
                },
                required=True,
                unknown="allow_extension",
            ),
            "domains": list_spec(str_spec(min_length=1), required=True, unique=True),
            "objectives": list_spec(str_spec(min_length=1), required=True),
            "environment": map_spec(str_spec(nullable=True), required=True),
            "constraints": map_spec(any_spec(), required=True),
            "verification": map_spec(enum_spec(VERIFICATION_LEVELS), required=True),
            "commands": map_spec(str_spec(nullable=True), required=True),
            "created_at": timestamp_spec(required=True),
            "updated_at": timestamp_spec(required=True),
        },
        unknown="allow_extension",
    ),
)

_PROFILE_HEADER = (
    "# SkillKernel project profile: authoritative machine-readable description of this project.\n"
    "# docs/project/PROFILE.md is generated from this file - edit the YAML, then run\n"
    "#   skillkernel profile render\n"
)


@dataclass(frozen=True)
class ProjectProfile:
    """A validated project profile."""

    name: str
    types: tuple[str, ...]
    domains: tuple[str, ...]
    objectives: tuple[str, ...]
    environment: dict[str, str | None]
    constraints: dict[str, Any]
    verification: dict[str, str]
    commands: dict[str, str | None]
    created_at: str
    updated_at: str
    raw: dict[str, Any]

    @classmethod
    def from_document(cls, document: Any, *, source: str | None = None) -> ProjectProfile:
        data = dict(PROFILE_SCHEMA.validate(document, source=source))
        project = data["project"]
        return cls(
            name=str(project["name"]),
            types=tuple(str(item) for item in project["type"]),
            domains=tuple(str(item) for item in data["domains"]),
            objectives=tuple(str(item) for item in data["objectives"]),
            environment={str(k): v for k, v in data["environment"].items()},
            constraints=dict(data["constraints"]),
            verification={str(k): str(v) for k, v in data["verification"].items()},
            commands={str(k): v for k, v in data["commands"].items()},
            created_at=str(data["created_at"]),
            updated_at=str(data["updated_at"]),
            raw=data,
        )

    def required_gates(self) -> tuple[str, ...]:
        return tuple(
            sorted(name for name, level in self.verification.items() if level == "required")
        )


def default_profile_document(project_name: str, *, now: str | None = None) -> dict[str, Any]:
    """A minimal, valid profile for a repository that has just been initialized.

    Every list starts empty on purpose: an invented domain is a fabricated
    requirement, and the kernel must not fabricate.
    """
    timestamp = now or now_iso()
    return {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "project": {
            "name": project_name,
            "type": [],
            "description": None,
        },
        "domains": [],
        "objectives": [],
        "environment": {},
        "constraints": {},
        "verification": {},
        "commands": {},
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def load_profile(layout: Layout) -> ProjectProfile:
    document = load_yaml_file(layout.profile_file)
    return ProjectProfile.from_document(document, source=layout.relative(layout.profile_file))


def write_profile(layout: Layout, document: dict[str, Any]) -> ProjectProfile:
    profile = ProjectProfile.from_document(document, source="profile")
    write_yaml_file(layout.profile_file, document, header=_PROFILE_HEADER)
    return profile


def render_profile_markdown(profile: ProjectProfile) -> str:
    """Render the human-readable projection of the profile.

    Generated, never hand-edited: the YAML is the single authoritative location
    for these facts.
    """
    lines: list[str] = [
        "<!-- GENERATED FILE - do not edit.",
        "     Source: docs/project/profile.yaml",
        "     Regenerate: skillkernel profile render -->",
        "",
        f"# Project profile: {profile.name}",
        "",
    ]
    description = profile.raw["project"].get("description")
    if description:
        lines += [str(description), ""]

    def bullet_section(title: str, values: tuple[str, ...]) -> None:
        lines.append(f"## {title}")
        lines.append("")
        if values:
            lines.extend(f"- {value}" for value in values)
        else:
            lines.append("_None recorded._")
        lines.append("")

    bullet_section("Project type", profile.types)
    bullet_section("Domains", profile.domains)
    bullet_section("Objectives", profile.objectives)

    def table_section(
        title: str, mapping: dict[str, Any], key_header: str, value_header: str
    ) -> None:
        lines.append(f"## {title}")
        lines.append("")
        if not mapping:
            lines.append("_None recorded._")
            lines.append("")
            return
        lines.append(f"| {key_header} | {value_header} |")
        lines.append("| --- | --- |")
        for key in sorted(mapping):
            value = mapping[key]
            rendered = "—" if value is None else str(value)
            lines.append(f"| `{key}` | {rendered} |")
        lines.append("")

    table_section("Environment", profile.environment, "Attribute", "Value")
    table_section("Constraints", profile.constraints, "Constraint", "Value")
    table_section("Verification gates", dict(profile.verification), "Gate", "Level")
    table_section("Commands", profile.commands, "Command", "Invocation")

    lines.append(f"_Profile created {profile.created_at}; last updated {profile.updated_at}._")
    lines.append("")
    return "\n".join(lines)


def write_profile_markdown(layout: Layout, profile: ProjectProfile) -> str:
    content = render_profile_markdown(profile)
    atomic_write_text(layout.profile_doc, content)
    return content


def set_profile_values(
    document: dict[str, Any], updates: dict[str, Any], *, now: str | None = None
) -> dict[str, Any]:
    """Apply a shallow update to a profile document and refresh ``updated_at``."""
    merged = dict(document)
    for key, value in updates.items():
        if key in {"schema_version", "created_at"}:
            raise ValidationError(f"{key} may not be modified through a profile update")
        merged[key] = value
    merged["updated_at"] = now or now_iso()
    return merged
