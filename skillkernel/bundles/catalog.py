"""Discovering bundles inside the installed package.

Access goes through :mod:`importlib.resources`, never through ``__file__`` or a
repository-relative path. That is the difference between a bundle that ships and
a bundle that only works in a checkout: the same code must read an asset from a
wheel in ``site-packages`` and from an editable source tree, and only the
resources API guarantees both.

This module reads and parses. It applies no installation policy and writes
nothing.
"""

from __future__ import annotations

from importlib.resources.abc import Traversable
from typing import Any

from skillkernel.bundles.model import (
    BUNDLE_CASE_SCHEMA,
    MANIFEST_FILENAME,
    Bundle,
    content_hash,
    validate_manifest,
)
from skillkernel.core.errors import RecordNotFoundError, ValidationError
from skillkernel.core.yamlio import load_yaml_text

__all__ = ["BUNDLE_ROOT", "available_bundles", "load_bundle", "packaged_root"]

BUNDLE_ROOT = ("assets", "skills")
"""Where bundles live inside the package."""

_POLARITIES = ("positive", "negative")


def packaged_root() -> Traversable:
    """The bundle directory inside the installed package."""
    from importlib.resources import files

    traversable: Traversable = files("skillkernel")
    for part in BUNDLE_ROOT:
        traversable = traversable / part
    return traversable


def _resolve(root: Traversable | None) -> Traversable:
    """Resolve the resource root to read from.

    ``root`` exists so the refusal paths below can be exercised against
    deliberately broken bundles, which cannot be shipped inside the package for
    obvious reasons. It is an internal seam, not a feature: nothing in the CLI
    or the installer passes it, so there is no user-facing way to install a
    bundle from an arbitrary directory.
    """
    return packaged_root() if root is None else root


def available_bundles(root: Traversable | None = None) -> list[str]:
    """Bundle identifiers shipped with this installation."""
    resolved = _resolve(root)
    if not resolved.is_dir():
        return []
    return sorted(entry.name for entry in resolved.iterdir() if entry.is_dir())


def _read_yaml(resource: Traversable, *, source: str) -> Any:
    try:
        text = resource.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValidationError(f"{source} could not be read from the package: {exc}") from exc
    document = load_yaml_text(text, source=source)
    if document is None:
        raise ValidationError(f"{source} is empty")
    return document


def load_bundle(bundle_id: str, *, root: Traversable | None = None) -> Bundle:
    """Load and validate one bundle from the installed package.

    The returned bundle's ``content_hash`` is **computed** from the portable
    files. If the manifest declares one, it must match: a declared hash is an
    integrity claim, and a mismatch means the shipped content is not what the
    bundle says it is.
    """
    resolved = _resolve(root)
    if not resolved.is_dir():
        raise RecordNotFoundError(
            "this installation ships no bundles; "
            f"expected package resources under skillkernel/{'/'.join(BUNDLE_ROOT)}"
        )
    directory = resolved / bundle_id
    if not directory.is_dir():
        available = ", ".join(available_bundles(root)) or "none"
        raise RecordNotFoundError(f"no bundle named {bundle_id!r}; available: {available}")

    manifest_resource = directory / MANIFEST_FILENAME
    if not manifest_resource.is_file():
        raise ValidationError(f"bundle {bundle_id!r} has no {MANIFEST_FILENAME}")

    manifest = validate_manifest(
        _read_yaml(manifest_resource, source=f"{bundle_id}/{MANIFEST_FILENAME}"),
        source=f"bundle {bundle_id!r}",
    )
    if manifest["bundle_id"] != bundle_id:
        raise ValidationError(
            f"bundle directory {bundle_id!r} contains a manifest declaring "
            f"bundle_id {manifest['bundle_id']!r}"
        )

    documents: dict[str, Any] = {MANIFEST_FILENAME: manifest}
    cases: dict[str, list[dict[str, Any]]] = {"positive": [], "negative": []}

    for polarity in _POLARITIES:
        folder = directory / "examples" / polarity
        if not folder.is_dir():
            continue
        for entry in sorted(folder.iterdir(), key=lambda item: item.name):
            if not entry.name.endswith(".yaml"):
                continue
            relative = f"examples/{polarity}/{entry.name}"
            case = dict(
                BUNDLE_CASE_SCHEMA.validate(
                    _read_yaml(entry, source=f"{bundle_id}/{relative}"),
                    source=f"{bundle_id}/{relative}",
                )
            )
            documents[relative] = case
            cases[polarity].append(case)

    if not cases["positive"]:
        raise ValidationError(f"bundle {bundle_id!r} has no positive evaluation cases")
    if not cases["negative"]:
        raise ValidationError(
            f"bundle {bundle_id!r} has no negative evaluation cases; without one its "
            "evaluation cannot measure false activation, which is the dangerous direction"
        )

    computed = content_hash(documents)
    declared = manifest.get("content_hash")
    if declared is not None and declared != computed:
        raise ValidationError(
            f"bundle {bundle_id!r} declares content_hash {declared} but its shipped "
            f"content hashes to {computed}. The package contents do not match the "
            "manifest's integrity claim."
        )

    return Bundle(
        bundle_id=bundle_id,
        bundle_version=str(manifest["bundle_version"]),
        content_hash=computed,
        manifest=manifest,
        positive_cases=tuple(cases["positive"]),
        negative_cases=tuple(cases["negative"]),
    )
