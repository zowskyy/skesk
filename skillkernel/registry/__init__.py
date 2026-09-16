"""Git-friendly record storage: a small index plus independent record files."""

from skillkernel.registry.index import (
    Registry,
    RegistryEntry,
    RegistryIndex,
    identifier_does_not_determine_destination,
)

__all__ = [
    "Registry",
    "RegistryEntry",
    "RegistryIndex",
    "identifier_does_not_determine_destination",
]
