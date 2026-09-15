# Freeze record — Milestone 0

```
STATUS:            VERIFIED BASELINE
MILESTONE:         0 (Stabilize and Prove the Foundation)
VERTICAL SLICE 1:  NOT STARTED
FROZEN:            2026-09-15T03:05:13Z
TAG:               skillkernel-m0-verified
```

This is a permanent rollback point. All later work is development *on top of*
this checkpoint, not continuation of an unstable partial build.

## Commit

| Item | Value |
| --- | --- |
| Content commit | `0b0a894bef560397120281caf42f84aa6af30de1` |
| Subject | `docs(skillkernel): split universal agent policy from project-specific rules` |
| Branch at freeze | `claude/skillkernel-design-geoez5` |
| Tag | `skillkernel-m0-verified` |

Every check below was run at `0b0a894` with a clean working tree. This freeze
record is the only change after it, and it adds documentation only — no code,
no configuration, no test. The tag points at the commit that adds this file, so
the tagged tree is `0b0a894` plus this record.

## Verified results

| Check | Command | Result |
| --- | --- | --- |
| Tests | `python -m pytest` | **PASS** — 421 passed, 0 failed, 0 skipped, 0 errors |
| Lint | `python -m ruff check .` | **PASS** — all checks passed |
| Format | `python -m ruff format --check .` | **PASS** — 59 files already formatted |
| Types | `python -m mypy` | **PASS** — no issues in 45 source files |
| Imports | walk + import every module | **PASS** — 31 of 31 |
| Offline | full suite under `unshare -rn` | **PASS** — 421 passed with no network namespace |
| Clean checkout | fresh clone → venv → install → all gates | **PASS** |

Integration tests: **NOT RUN** — none exist.
Acceptance tests: **NOT RUN** — none exist.

## Environment

| Item | Value |
| --- | --- |
| Python | 3.11.15 |
| Platform | Linux-6.18.44-fc-v33-x86_64-with-glibc2.39 |
| PyYAML | 6.0.3 (the only runtime dependency) |
| pytest | 9.1.1 |
| ruff | 0.16.7 |
| mypy | 2.3.1 |

Versions are those resolved at freeze time from the `dev` extra in
`pyproject.toml`, which pins lower bounds rather than exact versions. A future
reconstruction may resolve newer tool versions; if a gate fails after
reconstruction, compare against this table before assuming a regression in the
code.

Do not use system-level tool shims. On the machine where this baseline was
produced they resolved to a different interpreter under which `import pytest`
fails, and to different tool versions (ruff 0.15.8, mypy 1.19.1). Results from
them are not comparable to the table above.

## Restart procedure

```
git checkout skillkernel-m0-verified
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy
```

If those pass, you are back on known-good ground.

To resume development, branch from the tag rather than committing onto it:

```
git switch -c <new-branch> skillkernel-m0-verified
```

## What this baseline contains

Implemented, tested and type-checked: the schema engine, identifiers, atomic
writes, the registry, the project profile, knowledge records with lineage
invariants, experiments with freeze/revise/verdict derivation, the hash-chained
evidence ledger with its credential guard, observation records, the skill
contract, and the maturity state machine.

Production Python 4,123 lines; test Python 2,972 lines; documentation 979
lines.

## What this baseline does NOT contain

Stated explicitly so the checkpoint is not over-read:

- **No vertical slice runs.** The lifecycle — initialize → profile → knowledge
  → experiment → evidence → candidate → evaluate → promote → verify provenance
  — has never executed end to end.
- **No promotion gates.** The state machine validates *transitions*; it does not
  yet check the evidence that should justify one.
- **No CLI.** No command from the design directive is implemented.
- **No `SkillStore`, no `skillkernel init`, no bundled skill assets.** The
  universal agent policy (`DEC-0008`) therefore is not installable yet.
- **No integration or acceptance tests.** Every test is a unit test, so the
  subsystems have not been shown to compose.
- **Registry concurrency untested.** Only atomic writes are.
- **Tamper evidence is not immutability** (`DEC-0007`).

Full detail: `docs/project/baseline-0001.md` and `ARCHITECTURE.md`.

## Repository structure note

There is deliberately no `main` branch. The repository was created empty, so
the first pushed branch became the default. An empty `main` was **not** created
merely to look conventional — there is no technical need, and this tag is the
frozen baseline until the repository structure is decided.
