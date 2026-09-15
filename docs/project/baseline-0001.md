# Baseline 0001 — Milestone 0

The first trustworthy baseline for this repository. It replaces the unverified
state recorded at commit `0dc7a24` as the comparison point for later work.

Every value below was measured by running the command named, on the canonical
environment, at the commit named. Nothing here is transcribed from an earlier
run or inferred.

## Environment

| Item | Value |
| --- | --- |
| Python | 3.11.15 |
| Platform | Linux-6.18.44-fc-v33-x86_64-with-glibc2.39 |
| Environment | project virtualenv at `.venv`, created by `python3 -m venv` |
| Install | `.venv/bin/python -m pip install -e '.[dev]'` |

| Package | Version |
| --- | --- |
| mypy | 2.3.1 |
| pytest | 9.1.1 |
| PyYAML | 6.0.3 |
| ruff | 0.16.7 |

PyYAML is the only runtime dependency. The other three are development tools
declared in the `dev` extra of `pyproject.toml`.

**Note on tool provenance:** system-level tool shims exist on this machine at
different versions (ruff 0.15.8, mypy 1.19.1) and resolve to a different
interpreter, under which `import pytest` fails. Results from those shims are
not comparable to these and must not be mixed. All figures below come from
`.venv`.

## Results

| Check | Command | Result |
| --- | --- | --- |
| Package import | `python -c "import skillkernel"` | **PASS** |
| Submodule imports | `pytest tests/unit/test_package_imports.py` | **PASS** — 31 modules and subpackages, all import |
| Unit tests | `python -m pytest` | **PASS** — 421 passed, 0 failed, 0 skipped, 0 errors |
| Lint | `python -m ruff check .` | **PASS** — all checks passed |
| Format | `python -m ruff format --check .` | **PASS** — 56 files already formatted |
| Type check | `python -m mypy` | **PASS** — no issues in 45 source files |
| Integration tests | — | **NOT RUN** — none exist |
| Acceptance tests | — | **NOT RUN** — none exist |

Runtime: 3.54s for the full suite. Tests are offline and require no network,
model provider or external service.

## Test count by subsystem

| Subsystem | Tests |
| --- | --- |
| Schema engine | 68 |
| Skill contract and behaviour fingerprint | 66 |
| Maturity state machine | 51 |
| Experiments | 47 |
| Core primitives (ids, clock, text, YAML, layout) | 45 |
| Project profile, config and observations | 31 |
| Registry | 28 |
| Evidence ledger | 28 |
| Credential guard | 23 |
| Knowledge | 18 |
| Atomic writes | 12 |
| Package imports | 4 |
| **Total** | **421** |

## Code size

| Measure | Lines |
| --- | --- |
| Production Python (`skillkernel/`) | 4,123 |
| Test Python (`tests/`) | 2,970 |
| Ratio | 0.72 test lines per production line |

## Placeholder scan

Run over tracked files only:

```
git ls-files -z | xargs -0 grep -nE '\b(TODO|FIXME|XXX|HACK|NotImplementedError|placeholder|stub)\b'
```

**0 matches in code.** Matches inside `docs/`, `AGENTS.md`, `README.md` and
`ARCHITECTURE.md` are prose describing the no-placeholder rule itself.

Reviewed exceptions, each inspected individually:

| Location | Construct | Justification |
| --- | --- | --- |
| `skillkernel/utils/atomic.py:30` | bare `pass` | `except OSError: pass` around a best-effort directory `fsync`. Failure degrades durability, never correctness of the rename. Documented in the function's docstring. |
| `skillkernel/core/yamlio.py:26` | `# noqa: ARG002` | Overridden PyYAML method must keep its parameter name to match the base signature. |
| `tests/unit/test_package_imports.py:31` | `# noqa: BLE001` | Catching broad `Exception` is the point: the test reports *any* import failure. |
| `tests/unit/test_atomic_writes.py:162` | `# noqa: BLE001` | Collects failures from worker threads for assertion on the main thread. |
| `tests/unit/test_core_primitives.py:60` | `# type: ignore[arg-type]` | Deliberately passes an `int` where a `str` is required, to prove it is rejected. |
| `tests/unit/test_schema.py:33` | `# type: ignore[arg-type]` | `**kwargs` forwarding in a test schema builder. |

The `BLE001` rule was enabled specifically so these two broad catches are
justified suppressions rather than unnoticed ones. No blanket ignores exist,
and no rule was disabled to reach a green result.

## What this baseline does NOT establish

Stated explicitly so later work does not over-read it.

- **No vertical slice runs.** The full lifecycle (initialize → profile →
  knowledge → experiment → evidence → candidate → evaluate → promote → verify
  provenance) has never been executed end to end.
- **No promotion gates exist.** The maturity state machine validates
  *transitions*; it does not yet check the evidence that should justify one.
  Non-empty `do_not_apply_when`, passing evaluations and repeated evidence are
  specified but not enforced.
- **No CLI exists.** No command in the design directive is implemented.
- **No integration or acceptance tests exist.** Every test is a unit test.
  Subsystems have not been shown to compose.
- **Concurrency is tested only for atomic writes.** Registry-level concurrent
  writers are untested.
- **Tamper evidence is not immutability.** See `docs/decisions/DEC-0007`.

## Commit

Recorded at the commit that adds this file. Reproduce with:

```
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy
```
