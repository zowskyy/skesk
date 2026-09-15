"""Portable skill bundles.

A bundle is an immutable portable *definition*, never a portable record. It
carries what a skill tells a consumer to do; the receiving workspace owns the
identity, lifecycle and evidence.
"""

from skillkernel.bundles.model import (
    BUNDLE_SCHEMA,
    Bundle,
    content_hash,
)

__all__ = ["BUNDLE_SCHEMA", "Bundle", "content_hash"]
