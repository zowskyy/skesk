"""Interruption-safe file writes.

Registries and record files must never be observed half-written. Every write
goes through a temporary file in the *same* directory (so the final rename is a
same-filesystem, atomic ``rename(2)``), is flushed and ``fsync``-ed, and only
then replaces the destination. The containing directory is fsync-ed too, so the
rename itself survives a crash on POSIX filesystems.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from pathlib import Path

__all__ = ["atomic_write_bytes", "atomic_write_text", "atomic_write_via"]


def _fsync_dir(directory: Path) -> None:
    # Directory fsync is unavailable on some platforms (notably Windows);
    # skipping it there degrades durability but never correctness of the rename.
    try:
        fd = os.open(directory, os.O_RDONLY)
    except (NotADirectoryError, PermissionError, OSError):
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically, creating parent directories."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".skillkernel.tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        tmp_path.replace(path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    _fsync_dir(path.parent)


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Write ``text`` to ``path`` atomically."""
    atomic_write_bytes(path, text.encode(encoding))


def atomic_write_via(
    path: Path, render: Callable[[], bytes], validate: Callable[[bytes], None]
) -> None:
    """Render, validate, then atomically publish content.

    ``validate`` runs on the rendered bytes before anything touches ``path``; if
    it raises, the destination is left exactly as it was.
    """
    payload = render()
    validate(payload)
    atomic_write_bytes(path, payload)
