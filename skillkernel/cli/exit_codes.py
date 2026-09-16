"""Exit codes, per DEC-0010.

``INTEGRITY_FAILURE`` and ``INTERNAL_ERROR`` make different claims:

``1`` the kernel ran its validation machinery and found a problem — the report
      is trustworthy.
``70`` the kernel itself failed while checking — the report is NOT trustworthy,
      because an unknown number of checks never ran.

Collapsing them would report a repository whose true state is *unknown* as
merely *unhealthy*.
"""

from __future__ import annotations

__all__ = [
    "INTEGRITY_FAILURE",
    "INTERNAL_ERROR",
    "NOT_INITIALIZED",
    "OK",
    "USAGE_ERROR",
    "describe",
]

OK = 0
INTEGRITY_FAILURE = 1
USAGE_ERROR = 2
NOT_INITIALIZED = 3
INTERNAL_ERROR = 70  # BSD sysexits EX_SOFTWARE

_DESCRIPTIONS = {
    OK: "command completed successfully",
    INTEGRITY_FAILURE: "doctor completed and found an ERROR",
    USAGE_ERROR: "command-line usage error",
    NOT_INITIALIZED: "target is not an initialized SkillKernel workspace",
    INTERNAL_ERROR: "unexpected internal software failure",
}


def describe(code: int) -> str:
    return _DESCRIPTIONS.get(code, "unknown")


def help_epilog() -> str:
    lines = ["exit codes:"]
    lines.extend(f"  {code:<3} {describe(code)}" for code in sorted(_DESCRIPTIONS))
    return "\n".join(lines)
