"""Repository layout.

The layout is fixed rather than configurable. A configurable layout would make
every tool, document and test negotiate where things live; a fixed one makes
``skillkernel doctor`` able to state, unambiguously, that a file is in the wrong
place.

The marker file ``skillkernel.yaml`` at the repository root is what makes a
directory an initialized SkillKernel repository.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from skillkernel.core.errors import (
    NotInitializedError,
    UnsafeOperationError,
    ValidationError,
)

__all__ = ["CONFIG_FILENAME", "Layout", "find_root", "load_layout"]

CONFIG_FILENAME = "skillkernel.yaml"

SKILL_SCOPES = ("core", "project", "discovered")
"""Directory-backed skill scopes. ``deprecated`` is a maturity, not a scope.

Defined here rather than beside the skill schema because this is a *layout*
fact: it names directories. Everything else imports it from here, so a
location-bearing fact has exactly one definition.
"""

SKILL_FILENAME = "skill.yaml"
"""The record file inside every skill directory."""

SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
"""The grammar of a canonical skill slug.

This is not a new restriction. It is exactly what
:func:`skillkernel.utils.text.slugify` already produces -- lowercase ASCII
alphanumerics separated by single hyphens, with no leading or trailing hyphen --
and every slug in this repository already satisfies it.

The point is structural: a string matching this pattern cannot contain a path
separator, cannot be ``.`` or ``..``, and therefore cannot represent more than
one path component or escape its scope directory. Rejecting a malformed
component is stronger than joining arbitrary input and checking containment
afterwards, because the dangerous path is never constructed at all.
"""


MAX_COMPONENT_LENGTH = 128
"""Longest permitted path component.

Shape alone does not make a component usable. Every mainstream filesystem caps a
single name at 255 bytes, and the kernel appends a suffix such as ``.yaml``, so
an unbounded identifier that satisfies the grammar can still fail at the write
with a bare ``OSError`` -- *after* earlier writes have landed, which is exactly
the partial state the installer promises never to leave.

128 is conservative: it is comfortably under the limit on every filesystem this
runs on, and roughly four times the longest identifier this repository has ever
used (``blocked-but-first-attempt-underway``, 34 characters). The bound exists to
keep a refusal a refusal; it is not a style rule.
"""


def is_canonical_component(value: object) -> bool:
    """True when ``value`` is a well-formed, writable single path component."""
    return (
        isinstance(value, str)
        and len(value) <= MAX_COMPONENT_LENGTH
        and SLUG_PATTERN.match(value) is not None
    )


def validate_component(value: object, *, kind: str, boundary: str) -> str:
    """Return ``value`` if it is a canonical path component, else raise.

    One grammar, one bound, one message, for every identifier that becomes a
    directory or file name. ``kind`` and ``boundary`` only name the caller's
    concern in the diagnostic; the rule itself is identical, because the danger
    is identical.
    """
    if is_canonical_component(value):
        assert isinstance(value, str)
        return value
    if isinstance(value, str) and len(value) > MAX_COMPONENT_LENGTH:
        raise ValidationError(
            f"{kind} is {len(value)} characters, over the {MAX_COMPONENT_LENGTH}-character "
            f"limit for a single path component. Left unchecked it would fail at the "
            f"filesystem after earlier writes had already landed, turning a refusal into "
            f"partial state."
        )
    raise ValidationError(
        f"{value!r} is not a canonical {kind}. It must match "
        f"{SLUG_PATTERN.pattern} -- lowercase a-z0-9 separated by single hyphens, "
        f"with no leading or trailing hyphen. This keeps it to exactly one path "
        f"component, so it cannot escape its {boundary}."
    )


def is_canonical_slug(value: object) -> bool:
    """True when ``value`` is a well-formed skill slug."""
    return is_canonical_component(value)


def validate_slug(value: object) -> str:
    """Return ``value`` if it is a canonical slug, else raise.

    Called by the canonical path computation, so every caller inherits it.
    """
    return validate_component(value, kind="skill slug", boundary="scope directory")


def is_canonical_case_id(value: object) -> bool:
    """True when ``value`` is a well-formed evaluation case id."""
    return is_canonical_component(value)


def validate_case_id(value: object) -> str:
    """Return ``value`` if it is a canonical evaluation case id, else raise.

    A case id becomes a filename inside a skill's examples directory. It is
    validated by the same authority as a slug because it carries the same
    hazard -- and because every case id this repository has ever written already
    satisfies the grammar, so nothing legitimate is narrowed.

    Enforced on the *write* path only. ``load_evaluation_suite`` discovers cases
    by globbing the examples directory and never joins a case id onto a path, so
    a workspace holding a legacy non-canonical id still reads.
    """
    return validate_component(value, kind="evaluation case id", boundary="examples directory")


@dataclass(frozen=True)
class Layout:
    """Absolute paths to every managed location in a repository."""

    root: Path

    # --- top-level managed trees -------------------------------------------
    @property
    def config_file(self) -> Path:
        return self.root / CONFIG_FILENAME

    @property
    def skills_dir(self) -> Path:
        return self.root / "skills"

    @property
    def deprecated_skills_dir(self) -> Path:
        return self.skills_dir / "deprecated"

    @property
    def knowledge_dir(self) -> Path:
        return self.root / "knowledge"

    @property
    def experiments_dir(self) -> Path:
        return self.root / "experiments"

    @property
    def evidence_dir(self) -> Path:
        return self.root / "evidence"

    @property
    def observations_dir(self) -> Path:
        return self.root / "observations"

    @property
    def decisions_dir(self) -> Path:
        return self.root / "decisions"

    @property
    def docs_dir(self) -> Path:
        return self.root / "docs"

    @property
    def tmp_dir(self) -> Path:
        """Ephemeral working area; git-ignored by the generated .gitignore."""
        return self.root / ".skillkernel" / "tmp"

    # --- specific files -----------------------------------------------------
    @property
    def profile_file(self) -> Path:
        return self.docs_dir / "project" / "profile.yaml"

    @property
    def profile_doc(self) -> Path:
        return self.docs_dir / "project" / "PROFILE.md"

    @property
    def evidence_artifacts_dir(self) -> Path:
        return self.evidence_dir / "artifacts"

    @property
    def skills_index_file(self) -> Path:
        return self.skills_dir / "registry" / "index.yaml"

    def skill_scope_dir(self, scope: str) -> Path:
        if scope not in SKILL_SCOPES:
            raise ValueError(f"unknown skill scope {scope!r}; expected one of {SKILL_SCOPES}")
        return self.skills_dir / scope

    # --- canonical skill location ------------------------------------------
    #
    # A skill's location is derived from two declared fields, its scope and its
    # slug. This is the ONLY place that derivation happens: SkillStore and
    # doctor call these and compare, they never re-derive a path themselves.
    # Two computations of the same rule would be two sources of truth, which is
    # the class of defect this exists to prevent.

    def skill_path(self, scope: str, slug: str) -> Path:
        """The canonical absolute location of a skill's record file."""
        return self.skill_scope_dir(scope) / validate_slug(slug) / SKILL_FILENAME

    def relative_skill_path(self, scope: str, slug: str) -> str:
        """The canonical location, relative to the skills directory.

        This is the form the skills registry stores, so it is directly
        comparable with a registered path.
        """
        self.skill_scope_dir(scope)  # validates the scope
        return f"{scope}/{validate_slug(slug)}/{SKILL_FILENAME}"

    def parse_relative_skill_path(self, relative: str) -> tuple[str, str] | None:
        """Recover ``(scope, slug)`` from a registered path, or None if it is not canonical.

        Used to describe a mismatch in terms a reader can act on: which identity
        the stored location actually encodes.
        """
        parts = Path(relative).as_posix().split("/")
        if len(parts) != 3 or parts[2] != SKILL_FILENAME or parts[0] not in SKILL_SCOPES:
            return None
        if not is_canonical_slug(parts[1]):
            return None
        return parts[0], parts[1]

    def registry_file(self, domain_dir: Path) -> Path:
        return domain_dir / "registry" / "index.yaml"

    def records_dir(self, domain_dir: Path) -> Path:
        return domain_dir / "records"

    # --- managed-directory bookkeeping -------------------------------------
    def managed_directories(self) -> tuple[Path, ...]:
        """Every directory ``skillkernel init`` creates, in creation order."""
        skills = [self.skills_dir / scope for scope in SKILL_SCOPES]
        return (
            self.skills_dir,
            self.skills_dir / "registry",
            *skills,
            self.deprecated_skills_dir,
            self.knowledge_dir,
            self.knowledge_dir / "registry",
            self.knowledge_dir / "records",
            self.experiments_dir,
            self.experiments_dir / "registry",
            self.experiments_dir / "definitions",
            self.experiments_dir / "results",
            self.evidence_dir,
            self.evidence_dir / "registry",
            self.evidence_dir / "records",
            self.evidence_artifacts_dir,
            self.observations_dir,
            self.observations_dir / "registry",
            self.observations_dir / "records",
            self.decisions_dir,
            self.decisions_dir / "registry",
            self.decisions_dir / "records",
            self.docs_dir,
            self.docs_dir / "architecture",
            self.docs_dir / "project",
            self.docs_dir / "skills",
            self.docs_dir / "experiments",
            self.docs_dir / "decisions",
            self.docs_dir / "runbooks",
        )

    def relative(self, path: Path) -> str:
        """Return ``path`` relative to the repository root, as a POSIX string."""
        return Path(path).resolve().relative_to(self.root.resolve()).as_posix()

    def require_inside(self, path: Path) -> Path:
        """Reject any path that escapes the repository root.

        Every write the kernel performs goes through this, so a malformed
        registry entry such as ``../../etc/passwd`` cannot make the kernel write
        outside the repository it was pointed at.
        """
        resolved = Path(path).resolve()
        root = self.root.resolve()
        if resolved != root and root not in resolved.parents:
            raise UnsafeOperationError(f"{path} is outside the SkillKernel repository at {root}")
        return resolved

    def require_within(self, directory: Path, path: Path) -> Path:
        """Reject any path that escapes ``directory``, which must itself be managed.

        :meth:`require_inside` answers "is this in the workspace?". That is not
        the same question as "is this in the directory that owns it", and the
        difference is not academic: a file written to the workspace root instead
        of a skill's examples directory is still inside the workspace.

        This composes with :meth:`require_inside` rather than competing with it
        -- the owning directory is checked first -- so there is still exactly one
        implementation of "inside the repository".

        Guard the *final* destination with this, not the parent. A parent that
        passes containment tells you nothing about a child appended afterwards,
        which is precisely how an unvalidated component once escaped.
        """
        base = self.require_inside(directory)
        resolved = Path(path).resolve()
        if resolved != base and base not in resolved.parents:
            raise UnsafeOperationError(f"{path} is outside {self.relative(base)}")
        return resolved


def _candidate_roots(start: Path) -> Iterator[Path]:
    current = start.resolve()
    yield current
    yield from current.parents


def find_root(start: Path | None = None) -> Path:
    """Locate the nearest initialized SkillKernel repository at or above ``start``."""
    origin = Path(start) if start is not None else Path.cwd()
    for candidate in _candidate_roots(origin):
        if (candidate / CONFIG_FILENAME).is_file():
            return candidate
    raise NotInitializedError(
        f"no {CONFIG_FILENAME} found at or above {origin}; run 'skillkernel init' first"
    )


def load_layout(start: Path | None = None) -> Layout:
    return Layout(root=find_root(start))
