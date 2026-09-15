# Architecture

This document describes **what exists**. Planned work is confined to the final
section and is explicitly marked. Nothing here is inferred from the presence of
a file or a directory.

Authoritative as of Vertical Slice 1. The Milestone 0 baseline is frozen at
`skillkernel-m0-verified` (`docs/project/baseline-0001.md`); this document
describes the tree as it stands after Slice 1.

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

### 2.12 Workspace initialization — `skillkernel/project/bootstrap.py`

`initialize(root, project_name=...)` creates the managed tree, kernel
configuration, an empty project profile and every registry, and returns a
`Layout`. It refuses to overwrite an existing workspace and leaves unrelated
files alone.

The profile is created empty. A default that guessed at domains or objectives
would be a fabricated requirement.

Deliberately narrow: full `skillkernel init` semantics — reporting what was
created versus left alone, repairing a partial workspace, and the `doctor`
sweep — remain Slice 3. This exists because the lifecycle needed a real
workspace.

### 2.13 Skill storage — `skillkernel/skills/store.py`, `history.py`

Skills are directories, not files: `skill.yaml` plus promotion history,
evaluation definition and example cases. `SkillStore` writes the tree and
registers its `skill.yaml`.

`SkillStore` **cannot change a skill's maturity.** `update()` accepts only
behavioural fields and confidence; lifecycle and provenance fields are rejected
by name. Maturity moves exclusively through the promotion engine, because a
maturity change without a gate check and a history entry is the silent state
mutation the design forbids.

`history.yaml` is append-only. Its chain must be contiguous, start at
`observed`, and **end at the skill's current maturity** — so editing a maturity
into `skill.yaml` by hand is detectable rather than invisible.

**Location-bearing identity is immutable.** A skill's canonical location is
`skills/<scope>/<slug>/skill.yaml`, computed solely by `Layout`. Persistence
refuses a record whose declared `classification.scope` or `slug` would move it,
and the check precedes the first write, so a rejection leaves the workspace
byte-identical.

This is not tidiness. Before VS3, mutating either field left the record
registered at its old path, which freed the `(scope, slug)` pair, which let a
second skill be created in the same directory and silently overwrite the first
one's record and history — destroying its provenance, detected only by a later
read. Relocation is a future gated operation; `save()` is not it.
See `docs/decisions/DEC-0011-skill-location-identity.md`.

### 2.14 Evaluation — `skillkernel/evaluation/`

Deterministic, model-free scoring of a skill's activation boundaries.

A suite lives in the skill's own directory: `scorer/eval.yaml` (corpus, pass
threshold, false-activation guardrail) and `examples/positive|negative/*.yaml`.
**Both polarities are required** — a suite without negative cases can measure
whether a skill fires but never whether it fires when it should not.

Four outcome classes are reported separately and never collapsed (DEC-0003):
true positives, true negatives, **false activations**, missed activations. The
false-activation rate is a guardrail independent of accuracy, so a skill that
is 90% accurate while firing on a negative case still fails.

The runner writes a deterministic JSON report, records it in the evidence
ledger, and stamps it with the skill's **behaviour fingerprint** at evaluation
time. That stamp is what lets the `validated` gate ask whether an evaluation is
still about this skill.

### 2.15 Promotion — `skillkernel/promotion/`

Three checks in a fixed order: shape (the state machine), earned (the gate),
then record (history, then the skill). The gate runs before any write, so a
refused promotion leaves the skill untouched — there is no partial application
to unwind.

Gate requirements are in `docs/decisions/DEC-0009-slice1-gate-scope.md`. The
`validated` gate requires a passing evaluation whose fingerprint matches the
skill as it currently stands; a stale evaluation is refused with a diagnostic
saying so and naming the remedy.

### 2.16 Provenance — `skillkernel/validation/provenance.py`

Walks skill → evidence → experiment → knowledge → artifact, and checks the
reverse direction: every cited evidence record must resolve back to its own
experiment, knowledge and artifact. Detects dangling references, refuted or
superseded knowledge, unfrozen or post-freeze-edited experiments, tampered
artifacts and broken history chains.

---

### 2.17 Health check — `skillkernel/validation/doctor.py`

An *aggregator*. It owns no validation rules: every check delegates to a
function that already exists and is tested elsewhere, because a second
implementation of a rule is a second place for it to drift.

Its exception boundary keeps two similar-looking failures rigorously apart:

| Raised | Meaning | Becomes |
| --- | --- | --- |
| `SkillKernelError` | The kernel looked and found a problem. The report is trustworthy. | An `ERROR` finding |
| any other `Exception` | The kernel failed while looking. An unknown number of checks never ran. | An `internal_error`, separate from findings |

`BaseException` is deliberately not caught, so `KeyboardInterrupt` and
`SystemExit` keep their normal semantics. A report with zero findings but a
crashed validator is **not healthy** — it is *unknown*, and `is_complete` says
so.

`to_document()` carries no timestamps, and workspace-root paths are normalized
to a `<workspace>` token as findings are added, so the report is byte-stable
across runs and comparable across machines.

*That last property was claimed in VS2 but not delivered.* An `IntegrityError`
from `Registry.load` embeds an absolute path, and `doctor` stored the exception
text verbatim; VS2's test only exercised a healthy workspace, where no such
message arises. VS3 normalizes at the report boundary and tests the error path
under two different roots. Lower-level exceptions keep their absolute paths,
which are what a traceback needs. See
`docs/decisions/DEC-0012-workspace-independent-doctor-output.md`.

doctor also detects a stored scope/slug mismatch, a registered path that is not
structurally canonical, and any location registered to more than one skill. The
last two are derived from the *index alone, before any record is read*: a
malformed path usually makes its own record unreadable, so a load-first ordering
would skip exactly the entries that are worst — a defect found while writing
those tests.

The `skill-source` check validates any `provenance.x_source` a record declares,
against the same spec the installer writes through (DEC-0014). A skill with no
source block is not a finding.

It reports and never repairs: enforcement lives at the persistence boundary, and
a test asserts a corrupted workspace is byte-identical after a run.

**Known limitation, reported not fixed.** `Registry.orphan_record_files()` scans
`<domain>/records/`, which does not exist for skills — a skill lives in
`<scope>/<slug>/`. An orphaned *skill* directory left by a genuine I/O
interruption is therefore invisible to `doctor`. Readers resolve through the
index, so a partial skill is never *readable*; but the detectability half of
that guarantee is overstated for skills. See DEC-0015.

### 2.18 CLI — `skillkernel/cli/`, `skillkernel/__main__.py`

`argparse`, stdlib only. Three commands: `init`, `doctor` and
`skill install <bundle-id>`.

Exit codes are DEC-0010. The CLI is an adapter — it formats and chooses exit
codes, holding no domain logic — and that is enforced mechanically:
`tests/unit/test_cli_contract.py` parses the AST of every CLI module and fails
if it imports the registry, references `record_transition`/`promote`, writes
directly, or catches `BaseException`.

`python -m skillkernel` is a second surface onto the same adapter.

There is no `skill list`, `skill remove`, `skill update` or `skill search`. Each
would be a command whose behaviour is not yet decided, and a guess encoded in an
interface is harder to withdraw than one written down.

**Why this exists as its own slice.** At the end of VS1 the repository had 483
passing tests, clean lint and types, and a clean-checkout reproduction — while
the command declared in `[project.scripts]` did not run at all, because
`skillkernel/cli/` had been deleted during Milestone 0 and nothing ever executed
what was installed. The acceptance suite now drives the generated console
executable through `subprocess`, and a regression test reconstructs a broken
entry point to prove that check would catch it.

### 2.19 Portable bundles — `skillkernel/bundles/`, `skillkernel/assets/`

A **bundle** is an immutable portable *definition* — never a portable record.
It carries what a skill tells a consumer to do plus the static cases that let a
workspace evaluate it, and nothing that a workspace is supposed to earn.

| Module | Responsibility |
| --- | --- |
| `model.py` | The bundle schema, the frozen content hash, and the `x_source` spec. Refuses workspace-owned keys by name. |
| `catalog.py` | Reads and parses through `importlib.resources`. Applies no policy and writes nothing. |
| `installer.py` | Preflight, refusal policy, and one call each to the existing persistence, provenance and suite authorities. |

The hash was frozen *before* the first bundle existed, so the hashing contract
is a decision rather than an accident of whatever the first asset contained.

Everything that can fail for a bundle- or policy-related reason runs before the
first byte is written, including a dry run of the record the installer intends
to create under a placeholder identifier. A rejected install burns nothing —
not a directory, not a file, and not an identifier — and every refusal test
asserts that against a filesystem fingerprint, guarded against the vacuous
comparison of an empty tree with an empty tree.

The shipped bundle is the universal agent policy (DEC-0008), at
`skillkernel/assets/skills/two-method-escalation/`. It arrives at `observed`,
reaches `candidate` through the ordinary gate, and produces its own local
evaluation evidence. `experimental` stays correctly out of reach: it needs a
frozen experiment, which a bundle must never ship.

See DEC-0014 (what is portable), DEC-0015 (installation semantics) and DEC-0016
(packaged resources and the content hash).

---

## 3. Verification

796 tests, weighted by risk rather than by count. Negative and adversarial cases
are the majority.

| Area | Tests |
| --- | --- |
| Canonical slug grammar (red-team) | 70 |
| Promotion gates (red-team) | 22 |
| Slice 1 components | 36 |
| Lifecycle acceptance (end to end) | 4 |
| CLI boundary acceptance | 15 |
| Bundle acceptance (built wheel, clean interpreter) | 14 |
| Bundle catalog and refusals | 32 |
| Bundle content hash (frozen contract) | 28 |
| Bundle installer and zero-residue refusals | 26 |
| Source provenance, write side and read side | 25 |
| Doctor aggregation and exception boundary | 19 |
| CLI adapter contract | 17 |
| Skill location invariant | 42 |
| Doctor path normalization | 11 |
| Location acceptance (end to end) | 9 |
| Schema engine | 73 |
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

- **Skill relocation / cross-project promotion** — a skill's scope is now
  immutable (DEC-0011), so there is deliberately no way to move one. Changing a
  skill's scope requires an explicit, evidence-gated, recorded operation that
  does not exist yet.
- **Discovery** — deterministic candidate generation from repeated observations
  above configured thresholds. Generation is strictly separate from promotion.
- **Skill compiler** — a self-contained consumable package whose provenance
  references all resolve; incomplete provenance must fail compilation rather
  than fabricate.
- **`SKILL.md` generation** — `skill.yaml` is the authoritative structured
  truth and `SKILL.md` a generated projection, so no fact has two mutable homes.
  Staleness, absence and manual modification must all be detectable.
  See `docs/decisions/DEC-0005-generated-skill-docs.md`.
- **`skillkernel init --repair`** — `init` exists (§2.18) and refuses to
  overwrite an existing workspace. What remains is repairing a *partial*
  workspace and reporting created versus preserved paths.
- **CLI** — thin wrappers over the domain APIs. No business logic in command
  handlers. Commands will be added only once the operation beneath them exists.
- **Bundled core skills** — a small, high-confidence universal set. They will
  enter at `candidate`, not `validated`, because they carry no repository-local
  evidence. See `docs/decisions/DEC-0004-bundled-core-skills.md`.
  The first intended member is the universal agent policy — the two-method
  escalation rule and its companions — whose text already exists at
  `docs/policies/universal-agent-rules.md` but which has no installer yet.
  See `docs/decisions/DEC-0008-universal-agent-policy.md`.
- **Cross-project promotion** — a discovered skill may become core only after
  independent validation in several distinct projects. Repeated use inside one
  project is not evidence of universality.
