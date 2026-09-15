"""Exception hierarchy for SkillKernel.

Every failure the kernel raises on purpose is a :class:`SkillKernelError`, so
the CLI can turn it into a diagnostic and a stable exit code instead of a
traceback.
"""

from __future__ import annotations

from collections.abc import Sequence


class SkillKernelError(Exception):
    """Base class for all deliberate SkillKernel failures."""


class NotInitializedError(SkillKernelError):
    """Raised when no initialized SkillKernel repository can be located."""


class ValidationError(SkillKernelError):
    """Raised when a record does not satisfy its schema or an invariant."""

    def __init__(self, message: str, issues: Sequence[str] = ()) -> None:
        self.issues = list(issues)
        if self.issues:
            detail = "\n".join(f"  - {issue}" for issue in self.issues)
            message = f"{message}\n{detail}"
        super().__init__(message)


class IntegrityError(SkillKernelError):
    """Raised when stored state is internally inconsistent or tampered with."""


class DuplicateIdError(SkillKernelError):
    """Raised when an identifier would be reused."""


class RecordNotFoundError(SkillKernelError):
    """Raised when a referenced record does not exist."""


class TransitionError(SkillKernelError):
    """Raised when a maturity transition is not permitted."""


class GateError(SkillKernelError):
    """Raised when a promotion gate rejects a transition."""

    def __init__(self, message: str, reasons: Sequence[str] = ()) -> None:
        self.reasons = list(reasons)
        if self.reasons:
            detail = "\n".join(f"  - {reason}" for reason in self.reasons)
            message = f"{message}\n{detail}"
        super().__init__(message)


class CompilationError(SkillKernelError):
    """Raised when a skill cannot be compiled into a consumable package."""


class UnsafeOperationError(SkillKernelError):
    """Raised when an operation would overwrite or escape managed state."""
