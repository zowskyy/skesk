"""Text normalization helpers.

Discovery groups observations by *normalized* keys, so normalization has to be
deterministic and documented rather than incidental.
"""

from __future__ import annotations

import re
import unicodedata

__all__ = ["normalize_key", "slugify"]

_NON_SLUG = re.compile(r"[^a-z0-9]+")
_WHITESPACE = re.compile(r"\s+")


def slugify(value: str) -> str:
    """Return a lowercase, hyphen-separated slug suitable for a directory name."""
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    slug = _NON_SLUG.sub("-", ascii_only.lower()).strip("-")
    if not slug:
        raise ValueError(f"cannot derive a slug from {value!r}")
    return slug


def normalize_key(value: str) -> str:
    """Normalize a free-text classification/procedure label into a grouping key.

    Case, surrounding whitespace and internal whitespace runs are insignificant;
    everything else is preserved so that two genuinely different labels never
    collapse into one group.
    """
    return _WHITESPACE.sub(" ", value.strip()).lower()
