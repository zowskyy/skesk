"""Atomic write guarantees.

Scope note: these tests verify the guarantees the implementation *claims* --
that a destination is either the old content or the new content, never a
partial write, and that failures leave no debris. They do not simulate power
loss or filesystem-level crash states; that would test the kernel's OS, not the
kernel.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from skillkernel.utils.atomic import atomic_write_bytes, atomic_write_text, atomic_write_via

TEMP_GLOB = ".*.skillkernel.tmp"


def leftover_temp_files(directory: Path) -> list[Path]:
    return sorted(directory.glob(TEMP_GLOB))


def test_write_creates_the_file_with_exact_bytes(tmp_path: Path) -> None:
    target = tmp_path / "f.bin"
    payload = bytes(range(256))
    atomic_write_bytes(target, payload)
    assert target.read_bytes() == payload


def test_write_creates_missing_parent_directories(tmp_path: Path) -> None:
    target = tmp_path / "deeply" / "nested" / "f.txt"
    atomic_write_text(target, "hello")
    assert target.read_text() == "hello"


def test_write_replaces_existing_content_entirely(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    atomic_write_text(target, "a much longer original body")
    atomic_write_text(target, "short")
    assert target.read_text() == "short"


def test_text_is_written_as_utf8(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    atomic_write_text(target, "naïve — ✓")
    assert target.read_bytes() == "naïve — ✓".encode()


def test_successful_write_leaves_no_temporary_files(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    atomic_write_text(target, "x")
    assert leftover_temp_files(tmp_path) == []


def test_a_failure_during_replace_leaves_the_original_intact_and_no_debris(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "f.txt"
    atomic_write_text(target, "original")

    def explode(self: Path, other: object) -> None:
        raise OSError("simulated failure at the rename step")

    monkeypatch.setattr(Path, "replace", explode)
    with pytest.raises(OSError, match="simulated failure"):
        atomic_write_text(target, "replacement")

    assert target.read_text() == "original"
    assert leftover_temp_files(tmp_path) == []


def test_a_failure_while_writing_the_temporary_file_leaves_the_original_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "f.txt"
    atomic_write_text(target, "original")

    import os as os_module

    real_fsync = os_module.fsync

    def explode(fd: int) -> None:
        raise OSError("simulated failure before rename")

    monkeypatch.setattr(os_module, "fsync", explode)
    with pytest.raises(OSError, match="simulated failure"):
        atomic_write_text(target, "replacement")
    monkeypatch.setattr(os_module, "fsync", real_fsync)

    assert target.read_text() == "original"
    assert leftover_temp_files(tmp_path) == []


def test_atomic_write_via_validates_before_touching_the_destination(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    atomic_write_text(target, "original")

    def reject(_: bytes) -> None:
        raise ValueError("rendered content is not acceptable")

    with pytest.raises(ValueError, match="not acceptable"):
        atomic_write_via(target, lambda: b"replacement", reject)

    assert target.read_text() == "original"
    assert leftover_temp_files(tmp_path) == []


def test_atomic_write_via_publishes_when_validation_passes(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    atomic_write_via(target, lambda: b"accepted", lambda _: None)
    assert target.read_bytes() == b"accepted"


def test_the_temporary_file_is_created_beside_the_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cross-filesystem temp directory would make the final rename non-atomic."""
    target = tmp_path / "sub" / "f.txt"
    target.parent.mkdir()
    seen: list[str] = []

    import tempfile as tempfile_module

    real_mkstemp = tempfile_module.mkstemp

    def spy(
        suffix: str | None = None,
        prefix: str | None = None,
        # Named "dir" because that is mkstemp's keyword; the spy must match it.
        dir: str | os.PathLike[str] | None = None,
        text: bool = False,
    ) -> tuple[int, str]:
        seen.append(str(dir))
        return real_mkstemp(suffix, prefix, dir, text)

    monkeypatch.setattr(tempfile_module, "mkstemp", spy)
    atomic_write_text(target, "x")
    assert seen == [str(target.parent)]


def test_empty_content_is_written_without_error(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    atomic_write_bytes(target, b"")
    assert target.read_bytes() == b""
    assert target.exists()


def test_concurrent_writers_to_one_path_never_produce_partial_content(tmp_path: Path) -> None:
    """Interleaved writes must each land whole; readers never see a torn file."""
    import threading

    target = tmp_path / "f.txt"
    bodies = [str(index) * 5000 for index in range(8)]
    errors: list[BaseException] = []

    def write(body: str) -> None:
        try:
            for _ in range(10):
                atomic_write_text(target, body)
        except BaseException as exc:  # noqa: BLE001 - surfaced via the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(body,)) for body in bodies]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert target.read_text() in bodies
    assert leftover_temp_files(tmp_path) == []
