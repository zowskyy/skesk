# AGENTS.md

Operating instructions for agents working in this repository. This file is a
**map**, not an encyclopedia — it points at the authoritative documents rather
than restating them.

It has two parts, deliberately separated:

- **Part A — Universal agent rules.** Project-agnostic. Reusable verbatim in any
  repository. Full text: [`docs/policies/universal-agent-rules.md`](docs/policies/universal-agent-rules.md).
- **Part B — Project-specific rules.** Everything true only of SkillKernel.

Keeping them apart is what lets the universal half be lifted into another
project without dragging this project's assumptions along.

---

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

**Status: Milestone 0 and Vertical Slices 1–4 are verified.** Read
`ARCHITECTURE.md` before assuming a capability exists — an absent module is
absent on purpose.

---

# Part A — Universal agent rules

Summarized here because they are operational and must not require following a
link to learn. The linked policy is authoritative where the two differ.

## A1. Two-method escalation rule

On any blocker, ambiguity, failing implementation, unexplained test failure or
environment problem, you may attempt **at most two materially different
solution methods**. After two, stop and ask.

A method is a distinct technical approach — "repair the existing mechanism"
then "use the supported alternative mechanism" is two methods. Retrying the same
command, renaming a variable or tweaking the same patch is *one* method, and
does not reset the counter. A retry for a purely transient cause (file lock,
timeout) may be repeated once without counting.

After Method 1 fails: inspect, diagnose *why*, record the evidence, then pick a
genuinely different Method 2 and say what makes it different.

After Method 2 fails: **stop**. Do not try a third, rewrite large portions,
disable tests, weaken validation, remove safeguards, change acceptance criteria,
or move on to unrelated work. Report using the escalation format in the policy
document — problem, observed failure, both methods and why each failed, current
repository state, best diagnosis separating *known* from *likely* from
*unknown*, and the smallest question that unblocks you.

Research — reading code, logs, history, documentation — does not count as an
attempt. Investigate enough to make each method informed, but do not use
investigation to avoid escalating.

## A2. Stop immediately, before two attempts

If proceeding would require destructive or irreversible changes, deleting user
data, rewriting Git history, changing credentials, weakening security controls,
changing stated acceptance criteria, using paid services where free-only is
required, making a major architectural decision no existing decision record
covers, or guessing user intent where competing readings give materially
different systems.

## A3. No false verification

PASS / FAIL / SKIPPED / BLOCKED / NOT RUN are distinct. Never convert NOT RUN
into PASS. Never infer project-wide success from targeted checks. "It looks
correct" is not success. If still blocked after two methods, report
**BLOCKED — USER INPUT REQUIRED**, not "done".

## A4. No placeholder completion

A TODO-only file, an empty function, `NotImplementedError`, a command faking
success, or a result hardcoded to a fixture is not an implementation. Scan for
new `TODO`, `FIXME`, `pass`, `NotImplemented`, `placeholder`, `stub` before
reporting; inspect and explain every match. An empty directory asserts a
capability that is not there — do not create one ahead of its code.

## A5. Preserve user work

Operate only inside this repository. Before stopping to ask: leave no
experimental debris, revert failed changes where safe and clearly attributable,
preserve the evidence that explains the problem, never revert unrelated work,
and report any remaining dirty files.

---

# Part B — Project-specific rules

## B1. Authoritative locations

| Subject | Authoritative file |
| --- | --- |
| What is actually built | `ARCHITECTURE.md` |
| Universal agent policy | `docs/policies/universal-agent-rules.md` |
| Design decisions and rationale | `docs/decisions/` |
| Verified baseline measurements | `docs/project/baseline-0001.md` |
| Project facts (machine-readable) | `docs/project/profile.yaml` |
| Kernel policy (thresholds, gates) | `skillkernel.yaml` |

Each fact has exactly one authoritative home. If a document duplicates a fact
that lives elsewhere, the other copy is wrong by construction — remove the
duplication, do not sync it.

## B2. Setup

```
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

All tooling runs through that one environment. Do not use user-level tool
shims: they resolve to different interpreters and different tool versions, and
their results are not comparable.

## B3. Commands

| Purpose | Command |
| --- | --- |
| Lint | `.venv/bin/python -m ruff check .` |
| Format check | `.venv/bin/python -m ruff format --check .` |
| Type check | `.venv/bin/python -m mypy` |
| Unit tests | `.venv/bin/python -m pytest tests/unit` |
| Integration tests | `.venv/bin/python -m pytest tests/integration` (none exist yet) |
| Acceptance tests | `.venv/bin/python -m pytest -m acceptance` |
| Everything | `.venv/bin/python -m pytest` |

### The `skillkernel` command

```
skillkernel init <path>                       create a workspace (refuses to overwrite one)
skillkernel doctor <path>                     read-only integrity check; --json for a report
skillkernel skill install <bundle-id> [path]  install a bundled skill definition
```

Exit codes (DEC-0010). `1` and `70` make different claims and must not be
conflated: `1` means the checks ran and found a problem, so the report is
trustworthy; `70` means the kernel itself broke while checking, so the report is
incomplete and the workspace's true state is unknown.

```
0   command completed successfully
1   doctor found an ERROR, or a command was refused (a collision, a failed
    integrity claim, an unknown bundle)
2   command-line usage error
3   target is not an initialized SkillKernel workspace
70  unexpected internal software failure
```

The CLI is an adapter: it formats output and chooses exit codes, and holds no
domain logic. `tests/unit/test_cli_contract.py` enforces that by parsing its
AST, so reaching past the boundary fails the suite rather than a review.

## B4. Architectural invariants

Enforced by code and tests. Do not weaken one to make a change fit.

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

## B5. Project-specific stop conditions

In addition to A2, stop and ask before:

- adding a runtime dependency (PyYAML is currently the only one) — it needs a
  decision record;
- weakening a promotion gate, schema constraint or validator to make a test
  pass;
- hand-editing a registry index or record file to work around a missing API —
  that is a missing capability to report, not an obstacle to route around;
- disabling or narrowing the evidence-ledger credential guard;
- making the kernel's correctness depend on a network service, model provider
  or remote database (adapters may; the kernel may not);
- changing a record schema without bumping `schema_version` and recording the
  decision;
- describing the evidence ledger as immutable — it provides tamper *evidence*
  (`docs/decisions/DEC-0007`).

## B6. Definition of done

Each item actually executed, not assumed:

- [ ] `ruff check` passes with no new blanket ignores
- [ ] `ruff format --check` passes
- [ ] `mypy` passes without broad `Any` or unscoped `# type: ignore`
- [ ] tests pass, including negative cases for any new validation
- [ ] no placeholder introduced without written justification (A4)
- [ ] documentation describes what was built, not what is planned
- [ ] the working tree is clean

## B7. Recording experiments and evidence

Use the domain APIs, not hand-edited registry files. When something repeatedly
fails, classify the gap — missing knowledge, tool, abstraction, test,
validator, context, environment, skill, or an ambiguous specification — rather
than escalating the prompt. Repeated failure classes are what discovery later
turns into skill candidates.

## B8. Contradictory documentation

The authoritative file in B1 wins. Correct the non-authoritative copy in the
same change, and prefer deleting a duplicated fact over updating it twice. If
the authoritative source is itself wrong, fix it first and say so in the commit
message.
