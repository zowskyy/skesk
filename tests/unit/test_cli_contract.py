"""The CLI's structural contract, enforced mechanically.

"The CLI is only an adapter" is easy to write in a document and easy to violate
in a hurry. These tests make it a property of the code: the AST of every module
under ``skillkernel/cli/`` is parsed and checked, so a future change that
reaches past the boundary fails the suite rather than a review.

Also here: the requirement-#5 evidence that the acceptance boundary would have
caught the defect that prompted this slice.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest
import skillkernel.cli
from skillkernel.cli.exit_codes import (
    INTEGRITY_FAILURE,
    INTERNAL_ERROR,
    NOT_INITIALIZED,
    OK,
    USAGE_ERROR,
    describe,
)

CLI_DIR = Path(skillkernel.cli.__file__).parent


def cli_modules() -> list[Path]:
    modules = sorted(CLI_DIR.glob("*.py"))
    assert modules, "no CLI modules found; this test would pass vacuously"
    return modules


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def referenced_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Name):
            names.add(node.id)
    return names


# --- the adapter boundary --------------------------------------------------


@pytest.mark.parametrize("module", cli_modules(), ids=lambda p: p.name)
def test_the_cli_does_not_reach_into_the_registry(module: Path) -> None:
    """Storage internals are the library's business, never the CLI's."""
    offending = {name for name in imported_modules(module) if "skillkernel.registry" in name}
    assert not offending, f"{module.name} imports {offending}; the CLI must not touch the registry"


@pytest.mark.parametrize("module", cli_modules(), ids=lambda p: p.name)
def test_the_cli_never_mutates_skill_state(module: Path) -> None:
    """A maturity change from the CLI would bypass the gates entirely."""
    forbidden = {"record_transition", "promote", "deprecate", "attach_evidence", "refute"}
    used = referenced_names(module) & forbidden
    assert not used, (
        f"{module.name} references {sorted(used)}; the CLI may not mutate domain state. "
        "Those operations belong to the promotion engine and the stores."
    )


@pytest.mark.parametrize("module", cli_modules(), ids=lambda p: p.name)
def test_the_only_write_path_the_cli_uses_is_initialize(module: Path) -> None:
    writers = {"write_yaml_file", "atomic_write_text", "atomic_write_bytes", "put"}
    used = referenced_names(module) & writers
    assert not used, f"{module.name} writes directly via {sorted(used)}; use the library instead"


def test_the_cli_defines_no_validation_rules() -> None:
    """Validation lives in the library; the CLI reports what it is told."""
    for module in cli_modules():
        source = module.read_text(encoding="utf-8")
        assert "def _check_" not in source, f"{module.name} defines its own checks"
        assert "lineage_issues" not in source, f"{module.name} calls a validator directly"


# --- exit-code doctrine (DEC-0010) -----------------------------------------


def test_the_exit_codes_are_distinct() -> None:
    codes = [OK, INTEGRITY_FAILURE, USAGE_ERROR, NOT_INITIALIZED, INTERNAL_ERROR]
    assert len(set(codes)) == len(codes)


def test_internal_error_cannot_be_confused_with_a_finding_count() -> None:
    """70 sits far outside 0-3 on purpose."""
    assert INTERNAL_ERROR == 70
    assert max(OK, INTEGRITY_FAILURE, USAGE_ERROR, NOT_INITIALIZED) + 10 < INTERNAL_ERROR


def test_integrity_and_internal_failures_mean_different_things() -> None:
    assert describe(INTEGRITY_FAILURE) != describe(INTERNAL_ERROR)
    assert "found an ERROR" in describe(INTEGRITY_FAILURE)
    assert "internal" in describe(INTERNAL_ERROR)


def test_the_cli_does_not_catch_baseexception() -> None:
    """KeyboardInterrupt and SystemExit must retain normal semantics."""
    for module in cli_modules():
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and isinstance(node.type, ast.Name):
                assert node.type.id != "BaseException", (
                    f"{module.name} catches BaseException; catching it would swallow "
                    "KeyboardInterrupt and SystemExit"
                )


# --- requirement #5: evidence the boundary catches the real defect ---------


def test_a_broken_entry_point_would_fail_the_acceptance_boundary(tmp_path: Path) -> None:
    """Deterministic evidence, not a claim.

    This reconstructs the exact shape of the defect found at the end of VS1 — a
    console script whose target module does not exist — and demonstrates that
    the assertion the acceptance boundary makes (``returncode == OK``) rejects
    it. Before this slice, 483 tests and a clean-checkout reproduction were all
    green while the real script failed in precisely this way.
    """
    broken = tmp_path / "skillkernel-broken"
    broken.write_text(
        "#!/usr/bin/env python\n"
        "import sys\n"
        "from skillkernel.cli_that_does_not_exist.main import main\n"
        "sys.exit(main())\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(broken), "--help"], capture_output=True, text=True, check=False
    )

    assert result.returncode != OK, (
        "a console script whose module target is absent must not exit 0; if it "
        "did, the acceptance boundary could not detect this class of defect"
    )
    assert "ModuleNotFoundError" in result.stderr
    assert result.stdout == "", "a broken entry point produces no usable output"


def test_the_real_entry_point_is_not_broken_in_that_way() -> None:
    """The positive half of the pair: the declared module actually imports."""
    import importlib

    module = importlib.import_module("skillkernel.cli.main")
    assert callable(module.main)


def test_the_declared_entry_point_matches_the_implementation() -> None:
    """pyproject's console script target must keep resolving.

    The VS1 defect was exactly this drift: the declaration said
    skillkernel.cli.main:main while the module had been deleted.
    """
    import importlib
    import tomllib

    pyproject = Path(skillkernel.cli.__file__).parents[2] / "pyproject.toml"
    if not pyproject.is_file():  # pragma: no cover - installed, not a source tree
        pytest.skip("pyproject.toml not present next to the package")

    declared = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["scripts"]
    for command, target in declared.items():
        module_path, _, attribute = target.partition(":")
        module = importlib.import_module(module_path)
        assert hasattr(module, attribute), (
            f"pyproject declares {command} = {target!r}, but {module_path} has no {attribute}"
        )
