"""Reading and writing YAML records.

Two rules the whole kernel relies on:

* Loading always uses ``yaml.safe_load`` — records are data, never code.
* Dumping preserves key insertion order and never re-wraps, so a semantic change
  produces a small, reviewable Git diff.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from skillkernel.core.errors import ValidationError
from skillkernel.utils.atomic import atomic_write_text

__all__ = ["dump_yaml", "load_yaml_file", "load_yaml_text", "write_yaml_file"]


class _OrderedDumper(yaml.SafeDumper):
    """SafeDumper that keeps mapping order and indents lists readably."""

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:  # noqa: ARG002
        super().increase_indent(flow=flow, indentless=False)


def _represent_str(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_OrderedDumper.add_representer(str, _represent_str)


def load_yaml_text(text: str, *, source: str = "<string>") -> Any:
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValidationError(f"{source} is not valid YAML: {exc}") from exc


def load_yaml_file(path: Path) -> Any:
    """Load a YAML document, raising :class:`ValidationError` on bad input."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ValidationError(f"{path} does not exist") from exc
    except OSError as exc:
        raise ValidationError(f"{path} could not be read: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise ValidationError(f"{path} is not valid UTF-8: {exc}") from exc
    data = load_yaml_text(text, source=str(path))
    if data is None:
        raise ValidationError(f"{path} is empty")
    return data


def dump_yaml(data: Any) -> str:
    return yaml.dump(
        data,
        Dumper=_OrderedDumper,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
        width=100,
    )


def write_yaml_file(path: Path, data: Any, *, header: str | None = None) -> None:
    """Atomically write ``data`` as YAML, optionally preceded by a comment header."""
    body = dump_yaml(data)
    text = f"{header.rstrip()}\n{body}" if header else body
    atomic_write_text(Path(path), text)
