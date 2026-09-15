"""Every module must import.

This exists because it did not hold: ``skillkernel/skills/__init__.py`` once
imported a ``store`` module that was never written, and nothing caught it. A
package that does not import is not a package.
"""

from __future__ import annotations

import importlib
import pkgutil

import skillkernel


def _all_module_names() -> list[str]:
    return sorted(
        info.name for info in pkgutil.walk_packages(skillkernel.__path__, prefix="skillkernel.")
    )


def test_root_package_imports() -> None:
    assert importlib.import_module("skillkernel").__version__


def test_every_submodule_imports() -> None:
    failures: list[str] = []
    for name in _all_module_names():
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 - the point is to report any failure
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
    assert not failures, "modules failed to import:\n" + "\n".join(failures)


def test_package_discovery_found_the_expected_subsystems() -> None:
    """Guard against the walk silently finding nothing and passing vacuously."""
    names = set(_all_module_names())
    expected = {
        "skillkernel.core.schema",
        "skillkernel.registry.index",
        "skillkernel.knowledge.store",
        "skillkernel.experiments.store",
        "skillkernel.evidence.ledger",
        "skillkernel.discovery.observations",
        "skillkernel.skills.model",
        "skillkernel.skills.maturity",
        "skillkernel.project.profile",
    }
    assert expected <= names


def test_every_exported_name_resolves() -> None:
    """``__all__`` must not promise names the module does not define."""
    broken: list[str] = []
    for name in _all_module_names():
        module = importlib.import_module(name)
        for exported in getattr(module, "__all__", []):
            if not hasattr(module, exported):
                broken.append(f"{name}.__all__ lists {exported!r}, which is not defined")
    assert not broken, "\n".join(broken)
