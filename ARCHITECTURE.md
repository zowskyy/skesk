# Architecture

This document describes **what exists**. Planned work is confined to the final
section and is explicitly marked. Nothing here is inferred from the presence of
a file or a directory.

Authoritative as of the Milestone 0 baseline (`docs/project/baseline-0001.md`).

---

## 1. Shape of the system

Records are plain YAML files in a Git repository. There is no database, no
server and no daemon. Human inspection and reviewable diffs are load-bearing
properties, not conveniences.

Five record types are kept deliberately distinct:

| Type | Answers | Prefix |
| --- | --- | --- |
| Observation | What happened while doing the work? | `OBS-` |
| Knowledge | What do we believe is true? | `K-` |
| Experiment | What tested that belief, under what frozen rules? | `EXP-` |
| Evidence | What artifact backs this, and is it unchanged? | `EV-` |
| Skill | What should be done, and when — and when not? | `SKILL-` |

Collapsing these into one document is what produces untraceable prompt
folklore, so the separation is enforced by having separate schemas and stores.

---

## 2. Implemented

### 2.1 Schema engine — `skillkernel/core/schema.py`

A declarative validator over data loaded from YAML. Chosen over `jsonschema` and
`pydantic` for one reason that matters and two that follow from it: a per-object
**unknown-field policy**, dotted error paths for diagnostics, and no runtime
dependency. See `docs/decisions/DEC-0001-schema-engine.md`.

Three unknown-field policies:

- `reject` — any unrecognised key is an error (the default).
- `allow_extension` — keys prefixed `x_` are preserved as forward-compatible
  extensions; all other unknown keys are errors.
- `allow` — free-form, used only for opaque payloads such as environment
  fingerprints.

The engine never coerces. A field declared `str` holding an `int` is an error,
not a cast. `bool` is rejected where `int` is required, because `bool`
subclasses `int` in Python and a stray `true` would otherwise validate as `1`.
All violations in a document are reported together, not just the first.

### 2.2 Identifiers — `skillkernel/core/ids.py`

`PREFIX-NNNN`, zero-padded to at least four digits. Allocated from a monotonic
counter persisted in each registry index — never from `max(existing) + 1`.
A crash between allocation and write therefore *burns* a number rather than
risking reuse. Gaps are expected and legal; reuse is not, because provenance
chains outlive the records they point at.

### 2.3 Atomic writes — `skillkernel/utils/atomic.py`

Write to a temporary file in the same directory, flush, `fsync`, then
`rename`. The containing directory is `fsync`-ed so the rename itself survives a
crash on POSIX filesystems. A destination is therefore either its old content or
its new content, never a partial write.

**Guarantee boundary:** this is tested for failures at and before the rename
step, and for concurrent writers. It is not tested against power loss or
filesystem-level corruption, which would be testing the operating system.

### 2.4 Registry — `skillkernel/registry/index.py`

A small index file plus independent per-record files. One large mutable
document would mean colliding edits, unreadable diffs, and a partial write
destroying unrelated records.

A logical write touches two files and so cannot be atomic as a unit. The
**record file is always written first, the index second**. An interruption
therefore leaves an orphan record file — detectable, and harmless to readers,
who resolve records through the index. The reverse order would leave the index
pointing at a file that does not exist.

Detected corruption: orphan records, dangling entries, duplicate identifiers,
foreign prefixes, a counter that would reuse an identifier, an index declaring
the wrong kind, and index paths that escape the repository root.

### 2.5 Project profile — `skillkernel/project/profile.py`

Deliberately generic. `domains`, `objectives` and `project.type` are free lists
of strings; verification gates are a free-form mapping, so a project can declare
a gate the kernel has never heard of without a schema change. A default profile
records empty lists rather than plausible guesses — an invented domain is a
fabricated requirement.

`docs/project/PROFILE.md` is generated from `profile.yaml`.

### 2.6 Knowledge — `skillkernel/knowledge/`

Statuses: `proposed`, `supported`, `refuted`, `superseded`.

Enforced invariants: a refutation must state a reason; a refuted record cannot
be quietly restored to `supported`; `supersedes`/`superseded_by` are written as
a *pair*, so lineage is walkable in both directions and a one-sided link is
detectable. Records are never deleted.

`set_status` deliberately handles only `proposed` and `supported`. Reaching
`refuted` or `superseded` requires the dedicated operations, because those
states carry mandatory lineage that a generic setter could bypass.

### 2.7 Experiments — `skillkernel/experiments/`

Two properties make an experiment worth trusting, and both are mechanical:

**Freeze before you look.** A definition must be frozen — thresholds, metrics,
corpus and scorer fixed, and the whole hashed — before any result may be
recorded. Editing a frozen definition changes its hash; `verify()` reports it,
and further results are refused.

**The kernel computes the verdict.** The caller supplies measurements only.
`record_result` has no `verdict` parameter. Pass/fail/inconclusive is derived
from the frozen thresholds, so a gate cannot be moved once the numbers are in.
A guardrail violation forces `fail` regardless of the headline metric, and an
unmeasured guardrail counts as violated.

Changing a gate requires `revise()`, which writes version N+1 and leaves the
previous version's file byte-identical, so results recorded against it remain
interpretable.

Incoherent gate pairs (pass below failure on a maximised metric) are rejected at
load time.

### 2.8 Evidence ledger — `skillkernel/evidence/`

Each record carries the identifier and hash of its predecessor. Artifacts are
copied into `evidence/artifacts/<EV-id>/`, hashed and size-recorded.

**The exact guarantee**, stated carefully because promotion will rest on it:

> Unauthorized modification of ledger history becomes *detectable* by
> `verify()` under the current trust model.

That is tamper **evidence**, not immutability. An actor who can write to the
repository and recomputes every subsequent hash produces a ledger that verifies
— this is asserted by a test, not glossed over. The chain raises the cost of a
silent edit from changing one line to rewriting all remaining history, and makes
the ordinary accident loud. Real immutability needs an authority outside the
repository, such as signed commits or an append-only remote.

Detected: modified, deleted, inserted and reordered records; broken predecessor
hashes; corrupted, missing and stray artifacts.

**Credential guard.** Evidence is committed to Git, so content matching
high-confidence credential shapes is refused before an identifier is allocated
or a file written. The patterns are narrow by design: a guard that fires on
every hex string gets disabled, and a disabled guard protects nothing. False
positives on ordinary engineering text are tested for explicitly.

### 2.9 Observations — `skillkernel/discovery/observations.py`

Structured records of what happened during work: category, task, context,
classification, procedure used, outcome. Discovery will reason over these rather
than scraping prose. The two fields it groups on — `classification` and
`procedure_used` — are normalized deterministically, so "Flaky Import Order" and
"flaky import order" are one class and nothing else collapses.

### 2.10 Skills — `skillkernel/skills/`

**Contract** (`model.py`). A skill carries its procedure *and* its activation
boundaries. `do_not_apply_when` and machine-checkable `activation_rules` are
required fields of the contract, not optional documentation. Activation fails
closed: a skill with no rules never applies, and exclusions beat inclusions,
because over-activation is the dangerous direction.

**Field roles.** Every top-level field is classified `identity`,
`presentation`, `behavioral`, `lifecycle` or `provenance`. The behaviour
fingerprint covers exactly the `behavioral` fields. A test asserts the
classification covers the schema exactly, so a new field cannot be silently
omitted from the fingerprint.

This is what avoids a circularity: if lifecycle fields were covered, recording a
promotion would change the fingerprint and thereby invalidate the evaluation
that justified the promotion. Renaming a skill likewise cannot change what a
consumer following it would do, so `name` is excluded.
See `docs/decisions/DEC-0006-behavior-fingerprint.md`.

**Maturity state machine** (`maturity.py`):

```
observed → candidate → experimental → validated → trusted
                ↘            ↘             ↘          ↘
                          deprecated (terminal)
```

Eight transitions are legal; all 28 other ordered pairs are rejected, including
self-transitions and anything out of `deprecated`. `observed → deprecated` is
deliberately absent: an observed skill has asserted nothing a consumer could
have relied on.

### 2.11 Supporting utilities

- `core/clock.py` — all timestamps funnel through `now_iso()`, pinnable via
  `SKILLKERNEL_NOW` so records are reproducible. An invalid override is an
  error, not a silent fallback.
- `core/yamlio.py` — `safe_load` only; records are data and must never be able
  to construct a Python object. Dumps preserve key order for reviewable diffs.
- `core/paths.py` — fixed layout. `require_inside()` refuses any path escaping
  the repository root, so a malformed registry entry cannot direct a write
  outside it.
- `utils/hashing.py` — canonical JSON (sorted keys, fixed separators) so the
  same logical record always hashes identically.

---

## 3. Verification

421 tests, weighted by risk rather than by count. Negative and adversarial cases
are the majority.

| Area | Tests |
| --- | --- |
| Schema engine | 68 |
| Skill contract and fingerprint | 66 |
| Maturity state machine | 51 |
| Experiments | 47 |
| Core primitives | 45 |
| Registry | 28 |
| Evidence ledger | 28 |
| Credential guard | 23 |
| Project profile and observations | 31 |
| Knowledge | 18 |
| Package imports | 4 |

Commands are in `AGENTS.md`. Tests run offline.

---

## 4. Deviations from the original directive

Recorded rather than silently applied.

| Deviation | Reason |
| --- | --- |
| `skillkernel/skills/` package added | The skill contract and state machine are the central domain object; burying them in the generic `registry` package would misrepresent them. |
| Observations live in `skillkernel/discovery/` | Discovery is their only consumer. |
| Decisions will live under `skillkernel/project/` | Decision records are project policy. |
| `evidence/records/` added beside `evidence/artifacts/` | Evidence records need files; the directive's tree named only the artifact and registry directories. |
| `observations/` added at repository root | Observations are first-class records and need a home. |
| `skillkernel.yaml` at repository root | Kernel policy must be separate from project facts, and the file doubles as the root marker. |
| Hand-written schema engine | `DEC-0001`. |
| Profile stored at `docs/project/profile.yaml` | Keeps the machine-readable profile with its generated projection. |

---

## 5. Planned — NOT IMPLEMENTED

Everything below is design intent. None of it exists in the tree; the
corresponding directories were removed rather than left empty, because an empty
directory asserts a capability that is not there.

- **Promotion gates** — maturity-dependent required fields and artifacts
  (`candidate` requires purpose, applicability and provenance; `experimental`
  adds procedure and evidence; `validated` adds a passing evaluation, failure
  modes and verification; `trusted` requires repeated evidence across distinct
  corpora). Append-only promotion history with a contiguous, verifiable chain.
- **Evaluation** — deterministic scoring of activation boundaries against
  labelled positive and negative example fixtures. It must report activation
  true positives, true negatives, false activations and missed activations
  *separately* from execution success; a single aggregate score could hide unsafe
  over-activation. See `docs/decisions/DEC-0003-activation-scoring.md`.
- **Discovery** — deterministic candidate generation from repeated observations
  above configured thresholds. Generation is strictly separate from promotion.
- **Skill compiler** — a self-contained consumable package whose provenance
  references all resolve; incomplete provenance must fail compilation rather
  than fabricate.
- **`SKILL.md` generation** — `skill.yaml` is the authoritative structured
  truth and `SKILL.md` a generated projection, so no fact has two mutable homes.
  Staleness, absence and manual modification must all be detectable.
  See `docs/decisions/DEC-0005-generated-skill-docs.md`.
- **`skillkernel init`** — idempotent bootstrap that refuses destructive
  overwrites.
- **`skillkernel doctor`** — repository-wide integrity check reporting
  ERROR/WARNING/INFO with a non-zero exit on invalid state. Several of its
  checks already exist as library functions (`EvidenceLedger.verify`,
  `ExperimentStore.verify`, `KnowledgeStore.lineage_issues`,
  `Registry.orphan_record_files`); `doctor` will aggregate them.
- **CLI** — thin wrappers over the domain APIs. No business logic in command
  handlers. Commands will be added only once the operation beneath them exists.
- **Bundled core skills** — a small, high-confidence universal set. They will
  enter at `candidate`, not `validated`, because they carry no repository-local
  evidence. See `docs/decisions/DEC-0004-bundled-core-skills.md`.
- **Cross-project promotion** — a discovered skill may become core only after
  independent validation in several distinct projects. Repeated use inside one
  project is not evidence of universality.
