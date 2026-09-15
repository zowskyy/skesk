"""Content hashing used by the evidence ledger and integrity checks."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

__all__ = ["canonical_json", "sha256_bytes", "sha256_file", "sha256_mapping", "sha256_text"]

_CHUNK = 1024 * 1024


def canonical_json(value: Any) -> bytes:
    """Serialize ``value`` to a canonical, stable byte string.

    Keys are sorted and separators fixed so that the same logical record always
    hashes to the same digest regardless of how it was built or stored.
    """
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_mapping(value: Any) -> str:
    """Hash a structured record via its canonical JSON form."""
    return sha256_bytes(canonical_json(value))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()
