"""A conservative credential guard for anything entering the ledger.

Evidence is written to disk and committed. Section 23 of the design directive
forbids recording API keys, so the ledger refuses content that matches a small
set of high-confidence credential shapes rather than trusting the caller to have
checked. The patterns are intentionally narrow: a guard that fires on every
hex string would be disabled within a week, and a disabled guard protects
nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["CredentialMatch", "scan_bytes", "scan_text"]

_MAX_SCAN_BYTES = 4 * 1024 * 1024

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private-key-block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")),
    ("aws-access-key-id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("anthropic-api-key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("openai-api-key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{32,}\b")),
    (
        "assigned-secret",
        re.compile(
            r"(?i)\b(?:api[_-]?key|secret[_-]?key|access[_-]?token|client[_-]?secret|password)\b"
            r"\s*[:=]\s*[\"']?[A-Za-z0-9/+_=-]{16,}"
        ),
    ),
)


@dataclass(frozen=True)
class CredentialMatch:
    """One suspected credential, located by byte offset."""

    pattern: str
    offset: int

    def __str__(self) -> str:
        return f"possible {self.pattern} at byte offset {self.offset}"


def scan_text(text: str) -> list[CredentialMatch]:
    """Return every credential-shaped match in ``text``."""
    matches: list[CredentialMatch] = []
    for name, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            matches.append(CredentialMatch(pattern=name, offset=match.start()))
    return sorted(matches, key=lambda item: (item.offset, item.pattern))


def scan_bytes(data: bytes) -> list[CredentialMatch]:
    """Scan binary content, decoding leniently.

    Content above ``_MAX_SCAN_BYTES`` is scanned only up to that limit; the
    caller is told so by :func:`scan_truncated`.
    """
    window = data[:_MAX_SCAN_BYTES]
    return scan_text(window.decode("utf-8", errors="replace"))


def scan_truncated(data: bytes) -> bool:
    return len(data) > _MAX_SCAN_BYTES
