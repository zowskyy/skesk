# AGENTS.md

Operating instructions for agents working in this repository. This file is a
**map**, not an encyclopedia — it points at the authoritative documents rather
than restating them.

## What this project is

SkillKernel is a project-agnostic engineering intelligence layer. It stores
observations, knowledge, experiments, evidence and skills as version-controlled
records, and mechanically enforces the lifecycle that turns a one-off
observation into a trusted skill:

```
observation → hypothesis → experiment → evidence → knowledge
           → skill candidate → evaluation → validated skill → trusted skill
```

The kernel is infrastructure. It must never encode the engineering methods of
any one project; it is the mechanism that *learns* them.

## Status

**Milestone 0 (foundation) is verified. No vertical slice is implemented yet.**

Read `ARCHITECTURE.md` before assuming a capability exists. It separates
Implemented from Planned, and an absent module is absent on purpose.

## Authoritative locations

| Subject | Authoritative file |
| --- | --- |
| What is actually built | `ARCHITECTURE.md` |
| Design decisions and their rationale | `docs/decisions/` |
| Verified baseline measurements | `docs/project/baseline-0001.md` |
| Project facts (machine-readable) | `docs/project/profile.yaml` |
| Kernel policy (thresholds, gates) | `skillkernel.yaml` |

Each fact has exactly one authoritative home. If a document duplicates a fact
that lives elsewhere, the other copy is wrong by construction — fix the
duplication, do not sync it.

## Setup

```
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

All tooling runs through that one environment. Do not use user-level tool
shims: they resolve to different interpreters and different tool versions, and
results from them are not comparable.

## Commands

| Purpose | Command |
| --- | --- |
| Lint | `.venv/bin/python -m ruff check .` |
| Format check | `.venv/bin/python -m ruff format --check .` |
| Type check | `.venv/bin/python -m mypy` |
| Unit tests | `.venv/bin/python -m pytest tests/unit` |
| Integration tests | `.venv/bin/python -m pytest tests/integration` (none exist yet) |
| Acceptance tests | `.venv/bin/python -m pytest -m acceptance` (none exist yet) |
| Everything | `.venv/bin/python -m pytest` |

Tests must run offline. The kernel's correctness may not depend on any network
service, model provider or remote database.

## Architectural invariants

These are enforced by code and tests. Do not weaken one to make a change fit.

1. **The kernel verifies; the proposer proposes.** Schemas, state transitions,
   evidence requirements, promotion gates and integrity checks are deterministic
   code. An LLM may draft candidates; it may never be what makes them valid.
2. **Evidence precedes promotion.** A technique working once is not evidence.
3. **Knowledge, skills, experiments and decisions are distinct record types.**
   Do not collapse them into one document.
4. **Nothing is deleted.** Refuted knowledge stays refuted; superseded records
   keep their lineage; identifiers are never reused.
5. **Experiments freeze before results.** Gates may not move after measurements
   are known; a changed gate is a new experiment version.
6. **Writes are atomic.** Registries and records go through
   `skillkernel.utils.atomic`.
7. **The repository is the system of record.** Never rely on conversational
   memory for project knowledge.

## Repository boundaries

Operate only inside this repository. Do not modify global system
configuration, install unrelated global packages, write secrets into
repository files, or touch unrelated directories. Generated artifacts belong in
their documented locations; transient data belongs under `.skillkernel/tmp/`,
which is git-ignored.

The evidence ledger refuses content matching credential shapes. If it refuses
your record, redact the content — do not work around the guard.

## Definition of done

A change is done when all of the following hold, each actually executed:

- [ ] `ruff check` passes with no new blanket ignores
- [ ] `ruff format --check` passes
- [ ] `mypy` passes without broad `Any` or unscoped `# type: ignore`
- [ ] tests pass, including negative cases for any new validation
- [ ] no `TODO`, `FIXME`, `NotImplementedError`, placeholder or stub was
      introduced without an explicit written justification
- [ ] documentation describes what was built, not what is planned
- [ ] the working tree is clean

## Reporting

Report exactly what was executed. Distinguish **PASS / FAIL / SKIPPED /
BLOCKED / NOT RUN**, and never convert NOT RUN into PASS. Targeted tests
passing is not evidence that the project passes. If a milestone is incomplete,
say so and name what is missing.

Do not report a milestone complete because files exist. Demonstrate the
behaviour.

## Recording experiments and evidence

Use the domain APIs, not hand-edited registry files. If a task cannot be done
through a public API, that is a missing capability worth reporting — not a
reason to edit an index by hand.

When something repeatedly fails, classify the gap (missing knowledge, tool,
abstraction, test, validator, context, environment, skill, or an ambiguous
specification) rather than escalating the prompt. Repeated failure classes are
what discovery later turns into skill candidates.

## Contradictory documentation

If two documents disagree, the authoritative file in the table above wins.
Correct the non-authoritative copy in the same change, and prefer deleting a
duplicated fact over updating it in two places. If the authoritative source is
itself wrong, fix it first and say so in the commit message.
