"""Registry: a small index file alongside independent per-record files.

Design notes
------------
*Why not one big YAML file?* Every skill, experiment and evidence record would
then share a single mutable document: concurrent edits collide, diffs become
unreadable, and a partial write destroys unrelated records. Instead each record
is its own file and the index carries only identity, location and a short
denormalized summary used for listings.

*Write ordering.* A logical write touches two files, so it cannot be atomic as a
unit. The record file is always written **first** and the index second. An
interruption between them leaves an orphan record file — detectable by
``skillkernel doctor`` and harmless to every reader, because readers resolve
records through the index. The reverse order would leave the index pointing at a
file that does not exist, which breaks readers.

*Identifier allocation.* ``next_sequence`` is persisted before the record is
written. A crash therefore burns a number rather than risking reuse; gaps in the
sequence are expected and legal, reuse is not.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from skillkernel.core.errors import (
    DuplicateIdError,
    IntegrityError,
    RecordNotFoundError,
    ValidationError,
)
from skillkernel.core.ids import format_id, parse_id
from skillkernel.core.paths import Layout
from skillkernel.core.schema import (
    Schema,
    int_spec,
    list_spec,
    map_spec,
    object_spec,
    str_spec,
)
from skillkernel.core.yamlio import load_yaml_file, write_yaml_file

__all__ = ["INDEX_SCHEMA", "Registry", "RegistryEntry", "RegistryIndex"]

INDEX_SCHEMA_VERSION = 1

INDEX_SCHEMA = Schema(
    name="registry-index",
    supported_versions=(INDEX_SCHEMA_VERSION,),
    root=object_spec(
        {
            "schema_version": int_spec(required=True),
            "kind": str_spec(required=True, min_length=1),
            "next_sequence": int_spec(required=True, minimum=1),
            "entries": list_spec(
                object_spec(
                    {
                        "id": str_spec(required=True, min_length=1),
                        "path": str_spec(required=True, min_length=1),
                        "summary": map_spec(str_spec(nullable=True), required=False),
                    },
                    unknown="allow_extension",
                ),
                required=True,
            ),
        },
        unknown="allow_extension",
    ),
)

_INDEX_HEADER = (
    "# SkillKernel registry index. Generated and maintained by the kernel.\n"
    "# Entries are sorted by identifier; 'summary' is denormalized for listings only —\n"
    "# the record file named by 'path' is authoritative.\n"
)


@dataclass(frozen=True)
class RegistryEntry:
    """One row of a registry index."""

    id: str
    path: str
    summary: dict[str, str | None]

    def to_document(self) -> dict[str, Any]:
        document: dict[str, Any] = {"id": self.id, "path": self.path}
        if self.summary:
            document["summary"] = dict(self.summary)
        return document


@dataclass
class RegistryIndex:
    kind: str
    next_sequence: int
    entries: dict[str, RegistryEntry]

    def to_document(self) -> dict[str, Any]:
        return {
            "schema_version": INDEX_SCHEMA_VERSION,
            "kind": self.kind,
            "next_sequence": self.next_sequence,
            "entries": [self.entries[key].to_document() for key in sorted(self.entries)],
        }


class Registry:
    """Storage for one domain's records."""

    def __init__(
        self,
        layout: Layout,
        *,
        kind: str,
        domain_dir: Path,
        id_prefix: str,
        records_subdir: str = "records",
    ) -> None:
        self.layout = layout
        self.kind = kind
        self.domain_dir = domain_dir
        self.id_prefix = id_prefix
        self.records_subdir = records_subdir

    # --- locations ---------------------------------------------------------
    @property
    def index_file(self) -> Path:
        return self.domain_dir / "registry" / "index.yaml"

    @property
    def records_dir(self) -> Path:
        return self.domain_dir / self.records_subdir

    def resolve(self, relative_path: str) -> Path:
        """Resolve an index-relative path, refusing anything outside this domain.

        The boundary is the domain directory, not the repository. Asking only
        whether the result was in the workspace let an index entry such as
        ``../skills/core/x.yaml`` redirect one domain's write into another
        domain's tree -- inside the workspace, and nowhere near where it belongs.

        ``require_within`` still checks repository containment first (DEC-0017),
        so this narrows the boundary without relaxing it.
        """
        candidate = self.domain_dir / relative_path
        return self.layout.require_within(self.domain_dir, candidate)

    def relativize(self, path: Path) -> str:
        return Path(path).resolve().relative_to(self.domain_dir.resolve()).as_posix()

    def default_record_path(self, record_id: str) -> str:
        return f"{self.records_subdir}/{record_id}.yaml"

    # --- index -------------------------------------------------------------
    def exists(self) -> bool:
        return self.index_file.is_file()

    def create(self) -> None:
        """Create an empty index if one does not already exist (idempotent)."""
        if self.exists():
            return
        self._write_index(RegistryIndex(kind=self.kind, next_sequence=1, entries={}))

    def load_index(self) -> RegistryIndex:
        document = load_yaml_file(self.index_file)
        data = dict(INDEX_SCHEMA.validate(document, source=str(self.index_file)))
        if data["kind"] != self.kind:
            raise IntegrityError(
                f"{self.index_file} declares kind {data['kind']!r}, expected {self.kind!r}"
            )
        entries: dict[str, RegistryEntry] = {}
        for row in data["entries"]:
            record_id = str(row["id"])
            parse_id(record_id, self.id_prefix)
            if record_id in entries:
                raise DuplicateIdError(f"{self.index_file} lists {record_id} more than once")
            summary_raw = row.get("summary") or {}
            summary = {str(k): (None if v is None else str(v)) for k, v in summary_raw.items()}
            entries[record_id] = RegistryEntry(id=record_id, path=str(row["path"]), summary=summary)
        index = RegistryIndex(
            kind=self.kind, next_sequence=int(data["next_sequence"]), entries=entries
        )
        self._check_sequence_invariant(index)
        return index

    def _check_sequence_invariant(self, index: RegistryIndex) -> None:
        for record_id in index.entries:
            sequence = parse_id(record_id, self.id_prefix).sequence
            if sequence >= index.next_sequence:
                raise IntegrityError(
                    f"{self.index_file}: next_sequence={index.next_sequence} "
                    f"would reuse "
                    f"{record_id}; the counter must stay ahead of every allocated identifier"
                )

    def _write_index(self, index: RegistryIndex) -> None:
        document = index.to_document()
        INDEX_SCHEMA.validate(document, source=str(self.index_file))
        write_yaml_file(self.index_file, document, header=_INDEX_HEADER)

    # --- identifiers -------------------------------------------------------
    def allocate_id(self) -> str:
        """Reserve and persist the next identifier for this domain."""
        index = self.load_index()
        record_id = format_id(self.id_prefix, index.next_sequence)
        index.next_sequence += 1
        self._write_index(index)
        return record_id

    # --- records -----------------------------------------------------------
    def ids(self) -> list[str]:
        return sorted(self.load_index().entries)

    def entries(self) -> list[RegistryEntry]:
        index = self.load_index()
        return [index.entries[key] for key in sorted(index.entries)]

    def entry(self, record_id: str) -> RegistryEntry:
        index = self.load_index()
        try:
            return index.entries[record_id]
        except KeyError as exc:
            raise RecordNotFoundError(f"{record_id} is not registered in {self.kind}") from exc

    def has(self, record_id: str) -> bool:
        return record_id in self.load_index().entries

    def path_of(self, record_id: str) -> Path:
        return self.resolve(self.entry(record_id).path)

    def load(self, record_id: str) -> dict[str, Any]:
        document = load_yaml_file(self.path_of(record_id))
        if not isinstance(document, dict):
            raise ValidationError(f"{record_id} record is not a mapping")
        stored_id = document.get("id")
        if stored_id != record_id:
            raise IntegrityError(
                f"{self.path_of(record_id)} declares id {stored_id!r} "
                f"but is registered as {record_id}"
            )
        return document

    def load_all(self) -> Iterator[tuple[str, dict[str, Any]]]:
        for record_id in self.ids():
            yield record_id, self.load(record_id)

    def put(
        self,
        record_id: str,
        document: Mapping[str, Any],
        *,
        summary: Mapping[str, str | None] | None = None,
        relative_path: str | None = None,
        expect_new: bool = False,
        header: str | None = None,
    ) -> Path:
        """Write a record file and register (or re-register) it.

        The record file is written before the index, so an interruption can only
        ever leave an orphan file, never a dangling index entry.
        """
        parse_id(record_id, self.id_prefix)
        if document.get("id") != record_id:
            raise ValidationError(
                f"record document declares id {document.get('id')!r} "
                f"but is being stored as {record_id}"
            )
        index = self.load_index()
        if expect_new and record_id in index.entries:
            raise DuplicateIdError(f"{record_id} already exists in {self.kind}")
        sequence = parse_id(record_id, self.id_prefix).sequence
        if sequence >= index.next_sequence:
            index.next_sequence = sequence + 1

        existing = index.entries.get(record_id)
        path_value = relative_path or (
            existing.path if existing else self.default_record_path(record_id)
        )
        target = self.resolve(path_value)
        write_yaml_file(target, dict(document), header=header)

        index.entries[record_id] = RegistryEntry(
            id=record_id, path=path_value, summary=dict(summary or {})
        )
        self._write_index(index)
        return target

    def register_only(
        self,
        record_id: str,
        relative_path: str,
        *,
        summary: Mapping[str, str | None] | None = None,
    ) -> None:
        """Register a record whose file is managed elsewhere (e.g. a skill directory)."""
        parse_id(record_id, self.id_prefix)
        index = self.load_index()
        sequence = parse_id(record_id, self.id_prefix).sequence
        if sequence >= index.next_sequence:
            index.next_sequence = sequence + 1
        index.entries[record_id] = RegistryEntry(
            id=record_id, path=relative_path, summary=dict(summary or {})
        )
        self._write_index(index)

    def orphan_record_files(self) -> list[Path]:
        """Record files on disk that no index entry points at."""
        if not self.records_dir.is_dir():
            return []
        registered = {self.path_of(record_id).resolve() for record_id in self.ids()}
        found = sorted(p.resolve() for p in self.records_dir.glob("*.yaml"))
        return [path for path in found if path not in registered]
