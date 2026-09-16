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

## Status: Vertical Slice 4 — a skill can be shipped

The complete research-to-skill workflow now executes through the domain layer
and is proven by an acceptance test that starts from an empty directory:

```
initialize → knowledge → experiment (define, freeze, measure) → evidence
→ skill candidate → candidate → experimental → evaluate → validated
→ reload from disk → verify provenance
```

A skill proven in one repository can now be *delivered* to another. A bundle is
an immutable portable **definition** — never a portable record — so an installed
skill arrives with no identifier, maturity, evidence or history of its own, and
earns all of them locally:

```
install → observed → candidate (ordinary gate) → evaluate → local EV- evidence
experimental: correctly refused, because a bundle cannot ship an experiment
```

The command line covers the three operations whose behaviour is settled:

```
skillkernel init <path>                       create a workspace
skillkernel doctor <path>                     read-only integrity check (--json)
skillkernel skill install <bundle-id> [path]  install a bundled definition
```

Other commands remain unimplemented; each arrives only once the operation
beneath it is proven. In particular there is no `skill list`, `remove`, `update`
or `search` — an interface is harder to withdraw than a written-down guess.

Milestone 0's foundation is frozen at tag **`skillkernel-m0-verified`** — see
`docs/project/FREEZE-milestone-0.md` for its commit, measured results and
restart procedure.

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
| Workspace initialization | Implemented, tested |
| Skill storage and append-only promotion history | Implemented, tested |
| Activation-boundary evaluation | Implemented, tested |
| Promotion gates and engine | Implemented, tested |
| Provenance verification | Implemented, tested |
| Workspace health check (`doctor`) | Implemented, tested |
| CLI (`init`, `doctor`, `skill install`) | Implemented, tested |
| Portable bundles (format, content hash, installer) | Implemented, tested |
| Bundled universal agent policy | Implemented, tested |
| Bundle upgrade / removal | **Not implemented** — a collision is refused |
| Discovery, compiler, other CLI commands | **Not implemented** |

`ARCHITECTURE.md` is authoritative on this. Nothing above is inferred from the
presence of a file or directory.

Measured: 796 tests passing, ruff clean, mypy clean, reproducible from a clean
checkout — and, for the bundle, from a **built wheel installed into a fresh
interpreter that has never seen this repository**. See
`docs/project/baseline-0001.md` for the frozen Milestone 0 baseline.

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
                    evidence, evaluation, promotion, validation, discovery,
                    skills, bundles, project, utils)
skillkernel/assets/ portable skill bundles shipped with the package
tests/unit/         unit tests, weighted toward adversarial paths
tests/acceptance/   the end-to-end lifecycle proof
docs/decisions/     design decisions and their rationale
docs/policies/      reusable universal agent rules
docs/project/       project profile, baseline and freeze records
AGENTS.md           operating instructions for agents
ARCHITECTURE.md     what is implemented, and what is only planned
```

Record trees (`skills/`, `knowledge/`, `experiments/`, `evidence/`,
`observations/`, `decisions/`) are created inside a *consuming* repository when
it is initialized. They are not part of this repository's own source tree.

## Licence

MIT.
