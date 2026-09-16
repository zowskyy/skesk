"""Append-only promotion history.

Every maturity transition is appended to the skill's ``history.yaml``. The file
is never rewritten in place: :func:`append_transition` reads, appends and writes
back atomically, and :func:`history_issues` proves afterwards that the recorded
chain is contiguous, starts at ``observed``, and ends exactly where the skill
currently says it is.

That last check is the one that matters. It means a maturity cannot be edited
into a skill record without a matching recorded transition — silently rewriting
history becomes detectable rather than invisible.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from skillkernel.core.ids import EVIDENCE, SKILL
from skillkernel.core.schema import (
    Schema,
    any_spec,
    enum_spec,
    id_spec,
    int_spec,
    list_spec,
    map_spec,
    object_spec,
    str_spec,
    timestamp_spec,
)
from skillkernel.core.yamlio import load_yaml_file, write_yaml_file
from skillkernel.skills.maturity import MATURITIES

__all__ = [
    "HISTORY_SCHEMA",
    "append_transition",
    "history_issues",
    "load_history",
    "maturity_path",
    "new_history_document",
]

HISTORY_SCHEMA_VERSION = 1

HISTORY_SCHEMA = Schema(
    name="skill-history",
    supported_versions=(HISTORY_SCHEMA_VERSION,),
    root=object_spec(
        {
            "schema_version": int_spec(required=True),
            "skill": id_spec(SKILL, required=True),
            "transitions": list_spec(
                object_spec(
                    {
                        "previous_state": enum_spec(MATURITIES, nullable=True, required=True),
                        "new_state": enum_spec(MATURITIES, required=True),
                        "at": timestamp_spec(required=True),
                        "reason": str_spec(required=True, min_length=1),
                        "actor": str_spec(required=True, min_length=1),
                        "evidence": list_spec(id_spec(EVIDENCE), required=True),
                        "gate_result": map_spec(any_spec(), required=True),
                    },
                    unknown="allow_extension",
                ),
                required=True,
                min_items=1,
            ),
        },
        unknown="allow_extension",
    ),
)

_HEADER = (
    "# SkillKernel promotion history. Append-only: entries are never edited or removed.\n"
    "# The chain must be contiguous and end at the skill's current maturity.\n"
)


def new_history_document(skill_id: str, *, at: str, actor: str, reason: str) -> dict[str, Any]:
    """The opening entry every skill gets when it is created."""
    return {
        "schema_version": HISTORY_SCHEMA_VERSION,
        "skill": skill_id,
        "transitions": [
            {
                "previous_state": None,
                "new_state": "observed",
                "at": at,
                "reason": reason,
                "actor": actor,
                "evidence": [],
                "gate_result": {},
            }
        ],
    }


def load_history(path: Path) -> dict[str, Any]:
    document = load_yaml_file(path)
    return dict(HISTORY_SCHEMA.validate(document, source=str(path)))


def write_history(path: Path, document: dict[str, Any]) -> None:
    HISTORY_SCHEMA.validate(document, source=str(path))
    write_yaml_file(path, document, header=_HEADER)


def append_transition(
    path: Path,
    *,
    previous_state: str,
    new_state: str,
    at: str,
    reason: str,
    actor: str,
    evidence: Sequence[str] = (),
    gate_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append one transition. Existing entries are copied forward untouched."""
    document = load_history(path)
    document["transitions"] = [
        *document["transitions"],
        {
            "previous_state": previous_state,
            "new_state": new_state,
            "at": at,
            "reason": reason,
            "actor": actor,
            "evidence": list(evidence),
            "gate_result": dict(gate_result or {}),
        },
    ]
    write_history(path, document)
    return document


def maturity_path(document: dict[str, Any]) -> list[str]:
    """The states this skill has occupied, in order."""
    return [str(entry["new_state"]) for entry in document.get("transitions", [])]


def history_issues(document: dict[str, Any], *, skill_id: str, current_maturity: str) -> list[str]:
    """Verify the chain is contiguous, starts at ``observed``, and ends where the skill is."""
    issues: list[str] = []
    if document.get("skill") != skill_id:
        issues.append(f"history names skill {document.get('skill')!r} but belongs to {skill_id}")

    transitions = list(document.get("transitions") or [])
    if not transitions:
        return [*issues, f"{skill_id} has an empty promotion history"]

    first = transitions[0]
    if first.get("previous_state") is not None:
        issues.append(
            f"{skill_id} history begins with previous_state={first.get('previous_state')!r}; "
            "the first entry must have no predecessor"
        )
    if first.get("new_state") != "observed":
        issues.append(
            f"{skill_id} history begins at {first.get('new_state')!r}; "
            "every skill starts 'observed'"
        )

    for index in range(1, len(transitions)):
        previous = transitions[index - 1]
        current = transitions[index]
        if current.get("previous_state") != previous.get("new_state"):
            issues.append(
                f"{skill_id} history entry {index} claims to start from "
                f"{current.get('previous_state')!r}, but the preceding entry ended at "
                f"{previous.get('new_state')!r}"
            )
        if str(current.get("at", "")) < str(previous.get("at", "")):
            issues.append(
                f"{skill_id} history entry {index} is timestamped before the entry preceding it"
            )

    final_state = transitions[-1].get("new_state")
    if final_state != current_maturity:
        issues.append(
            f"{skill_id} is recorded as {current_maturity!r} but its history ends at "
            f"{final_state!r}; the maturity was changed without a recorded transition"
        )
    return issues
