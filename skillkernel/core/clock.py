"""Timestamps.

Timestamps are the only unavoidable source of nondeterminism in the kernel, so
they are funnelled through one function that tests (and reproducible runs) can
pin via the ``SKILLKERNEL_NOW`` environment variable.
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime

_ISO_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

NOW_ENV_VAR = "SKILLKERNEL_NOW"


def now_iso() -> str:
    """Return the current UTC time as ``YYYY-MM-DDTHH:MM:SSZ``.

    If ``SKILLKERNEL_NOW`` is set it must already be in that exact form; an
    invalid override is an error rather than a silently ignored value.
    """
    override = os.environ.get(NOW_ENV_VAR)
    if override is not None:
        if not _ISO_UTC.match(override):
            raise ValueError(f"{NOW_ENV_VAR} must look like 2024-01-31T12:00:00Z, got {override!r}")
        return override
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_timestamp(value: str) -> bool:
    """Return True if ``value`` is a well-formed kernel timestamp."""
    if not _ISO_UTC.match(value):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return True
