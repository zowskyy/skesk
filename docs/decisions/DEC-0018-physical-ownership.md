# DEC-0018 — Physical state does not acquire logical ownership by being there

**Status:** Accepted, implemented, tested (Vertical Slice 5).
**Amends:** DEC-0015 (the safety reading, not the index-read claim).
**Relates to:** DEC-0011, DEC-0012.

## Problem

Two defects, measured at the hardened VS4 checkpoint `cec6576` and reproduced in
disposable workspaces before any code was changed. They share a filesystem and
nothing else: one is about *mutation*, the other about *observation*, and fixing
either would have left the other exactly as it was.

### Mutation — creation adopted whatever was already there

`SkillStore.create` called `mkdir(parents=True, exist_ok=True)` and wrote. Its
only collision check, `find_by_slug`, resolves through the index, so it could not
see an unregistered directory. Measured against an unindexed
`skills/core/sample-skill/`:

- `skill.yaml` and `history.yaml` were **overwritten**;
- `examples/` **survived**;
- `doctor` reported **nothing, before or after**.

The surviving examples are what makes this a provenance failure rather than a
housekeeping one. `load_evaluation_suite` globs the examples directory, so the
new skill's suite loaded **four cases where two had been authored** — two of them
belonging to the skill that had just been destroyed. A skill inheriting evidence
it never earned is the one thing this project exists to prevent.

The bundle installer already refused the identical topology, with zero bytes
written. Two write paths into the same tree, disagreeing.

### Observation — orphan discovery looked in the wrong place

`Registry.orphan_record_files()` globbed `<domain>/<records_subdir>/*.yaml`,
non-recursively. For skills that is `skills/records/`, which is never created; for
experiments it is one directory above where definitions actually live. Measured:

| Planted | A shape the store writes? | Detected |
| --- | --- | --- |
| `definitions/EXP-0099/v1.yaml` | yes | **no** |
| `definitions/EXP-0098.yaml` | never | yes |

It detected exactly the shapes that cannot occur and missed exactly the ones that
can. Two of five domains were blind; only the skills half was documented.

## Decision

### A — a create refuses a destination it does not already own

Before any mutation and before `allocate_id`, a create proves its canonical
destination does not physically exist. `SkillStore.require_unowned_destination`
is that rule, and it is one rule: `SkillStore.create` and the bundle installer's
`preflight` both call it, rather than keeping a copy each.

Refusal guarantees the workspace is byte-identical, no identifier is burned, and
no record, history, evaluation or index state is created. The ordering is
asserted behaviourally — every mutation on the create path is replaced with a
detonator and the refusal must still arrive — rather than by where the call sits
in the source.

`exists()` follows symlinks, so a **broken** symlink read as absent and slipped
past into a late refusal from `require_inside`, after an identifier had been
burned. Found in the red-team pass; `is_symlink()` is checked too.

**Adoption is deliberately absent.** Declaring an unindexed directory to belong to
a freshly allocated record needs an explicit protocol with provenance rules of
its own. `create` is not that protocol, and no repair, migration or cleanup
behaviour is introduced anywhere.

### B — each domain is asked about its own topology

`Registry` takes a record finder. `None` keeps the flat collection, which is what
knowledge, evidence and observations genuinely are. Skills and experiments pass
an enumerator from `Layout`, which already owns physical derivation and is the
only place it happens. `doctor` remains an aggregator and learns no filesystem
shape; it calls `orphan_states()` and reports what comes back.

**One ownership rule covers every topology:** a candidate is owned when a
registered record path *is* the candidate, or lies *inside* it. A flat record
file matches itself; a skill directory is owned by the `skill.yaml` within it; an
experiment directory is owned by whichever version the index currently names.

That last case is why the experiment unit is the directory. `revise` writes
`v2.yaml` and repoints the index, leaving `v1.yaml` unreferenced but entirely
legitimate — results were recorded against it and it is deliberately never
rewritten. Enumerating definition *files* would have reported an orphan on every
revised experiment. The false positive is what fixed the unit.

## What counts as an orphan, and what does not

Bounded on purpose. `doctor` reports recoverable-looking managed state that no
index owns; it is not a filesystem linter, and "unexpected on disk" is not a
synonym for "record".

A skill orphan requires **all** of: a real scope, a canonical slug, the canonical
`<scope>/<slug>/` location, at least one managed artifact (`skill.yaml` or
`history.yaml`), and no index ownership. `history.yaml` alone qualifies because
that is precisely what an interrupted `create` leaves — the history is written
first — so the state Invariant A now refuses to adopt is the same state Invariant
B makes visible.

| Ignored | Why |
| --- | --- |
| `Not_A_Slug/`, `-leading-hyphen/`, `under_score/` | not canonical components (DEC-0013) |
| empty directories, `junk.txt`, `loose.yaml` | no managed artifact |
| debris nested inside a skill | the owning skill is indexed |
| anything under `skills/deprecated/` | `deprecated` is a maturity, not a scope |

Severity stays **WARNING**. An orphan is recoverable and must not fail the gate;
detectability is not a reason to invent a new error policy.

## Compatibility

Two intentional behaviour changes, neither disguised as an implementation detail:

1. `SkillStore.create` refuses a pre-existing unowned canonical destination where
   it previously adopted and partly overwrote it.
2. Canonical record-shaped skill and experiment state that was previously
   invisible now produces orphan `WARNING`s.

Existing flat-domain behaviour is unchanged, and the legacy flat
`definitions/<EXP-ID>.yaml` detection is retained rather than traded away for the
real topology — withdrawing a detection is not a repair.

## Reported, not repaired

The bounded structural search found the same adoption shape in the record
domains: a planted `knowledge/records/K-0001.yaml` or
`experiments/definitions/EXP-0001/v1.yaml` is silently overwritten by the next
`add()`. It is a genuine instance of Invariant A and it is **not** fixed here,
because `require_unowned_destination` is skill-specific and widening it was not
authorized. Its reachability is materially lower: the skill case collides on a
caller-chosen slug derived from a name, while these collide only on a
monotonically allocated identifier that would have to be replayed. Invariant B
does make all of them visible to `doctor`.

A second contamination route remains reachable only by editing an *index-owned*
skill's own directory out of band. That is external modification of owned state,
not adoption of unowned state, and `load_evaluation_suite` is therefore left
unchanged: the route that mattered was closed at its source.
