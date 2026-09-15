# SkillKernel

A project-agnostic engineering intelligence layer.

SkillKernel lets a repository start with a small set of universal engineering
skills and progressively discover, evaluate, validate and reuse project-specific
ones — based on evidence generated during development, rather than on a
continually growing instruction document.

The intended lifecycle:

```
observation → hypothesis → experiment → evidence → knowledge
           → skill candidate → evaluation → validated skill → trusted skill
```

The kernel is deliberately not an application feature. It is the mechanism that
learns a project's engineering methods; it does not contain any project's
methods itself. The same kernel should serve an animation studio, a website and
an unrelated future repository while each develops a different skill library.

## Status: Milestone 0 — foundation verified (frozen)

Frozen at tag **`skillkernel-m0-verified`**. See
`docs/project/FREEZE-milestone-0.md` for the commit hash, measured results,
tool versions and restart procedure. Resume work by branching from that tag,
not by committing onto it.

The domain layer is implemented, tested and type-checked. **No end-to-end
vertical slice runs yet**, and there is no command-line interface.

| Subsystem | State |
| --- | --- |
| Schema engine, identifiers, atomic writes | Implemented, tested |
| Registry (index + independent records) | Implemented, tested |
| Project profile | Implemented, tested |
| Knowledge records and lineage | Implemented, tested |
| Experiments (freeze, revise, results) | Implemented, tested |
| Evidence ledger (hash-chained) | Implemented, tested |
| Observation records | Implemented, tested |
| Skill contract and maturity state machine | Implemented, tested |
| Promotion gates, evaluation, discovery, compiler, CLI | **Not implemented** |

`ARCHITECTURE.md` is authoritative on this. Nothing above is inferred from the
presence of a file or directory.

Measured on the baseline commit: 421 tests passing, ruff clean, mypy clean.
See `docs/project/baseline-0001.md`.

## Development setup

Requires Python 3.11 or newer.

```
git clone <this repository>
cd skesk
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

All dependencies come from `pyproject.toml`; PyYAML is the only runtime
dependency.

## Verification

Run everything through the project environment so results are comparable:

```
.venv/bin/python -m ruff check .            # lint
.venv/bin/python -m ruff format --check .   # formatting
.venv/bin/python -m mypy                    # type check
.venv/bin/python -m pytest                  # tests
```

Tests are offline and deterministic. Timestamps are pinned through
`SKILLKERNEL_NOW`, so records are byte-for-byte reproducible.

## Design principles

- **The kernel verifies; the proposer proposes.** Schema validation, state
  transitions, evidence requirements and promotion gates are deterministic
  code. A model may draft a skill candidate; it is never what makes one valid.
- **Evidence before promotion.** A technique working once is not evidence.
- **Truth and procedure are separate.** Knowledge records say what is believed
  to be true; skills say what to do; experiments test; decisions record policy.
- **Nothing is deleted.** Refuted knowledge keeps its identifier. Identifiers
  are never reused.
- **Mechanical enforcement over prose.** If a rule can be checked, it is checked.
- **Model-agnostic.** No part of the record format, evidence model or
  lifecycle depends on a particular model or agent runtime.

## Repository layout

```
skillkernel/        the kernel (core, registry, knowledge, experiments,
                    evidence, discovery, skills, project, utils)
tests/              unit tests; integration and acceptance to follow
docs/decisions/     design decisions and their rationale
docs/project/       project profile and baseline records
AGENTS.md           operating instructions for agents
ARCHITECTURE.md     what is implemented, and what is only planned
```

Record trees (`skills/`, `knowledge/`, `experiments/`, `evidence/`,
`observations/`, `decisions/`) are created inside a *consuming* repository when
it is initialized. They are not part of this repository's own source tree.

## Licence

MIT.
