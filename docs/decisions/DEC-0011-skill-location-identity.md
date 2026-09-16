# DEC-0011 — A skill's location-bearing identity is immutable

**Status:** Accepted, implemented, tested (Vertical Slice 3).

## Problem

A skill's canonical location is derived from two declared fields:

```
skills/<classification.scope>/<slug>/skill.yaml
```

Ordinary persistence accepted a change to either field while leaving the record
registered at its original path. This was demonstrated through public APIs, in a
temporary workspace, at commit `e1c39df`:

```
[1] create        SKILL-0001  scope=project  slug='target-skill'
                  path=skills/project/target-skill/skill.yaml
[2] mutate        classification.scope: 'project' -> 'core'
[3] save()        ACCEPTED
[4] reload        declared scope=core, registered path unchanged
[5] lookup        in_scope('core')=['SKILL-0001'], in_scope('project')=[]
                  find_by_slug('project','target-skill')=None
[6] doctor        0 errors                                  <-- undetected
[7] collision     second create ADMITTED as SKILL-0002, SAME PHYSICAL FILE
[8] overwrite     skill.yaml, history.yaml, registry index silently rewritten
[9] load          IntegrityError -- SKILL-0001 unrecoverable, provenance destroyed
```

`slug` mutation reproduces the identical sequence. This is data loss through
public APIs, detected only *after* the destruction, by a later read.

## Decision

**`classification.scope` and `slug` are immutable through ordinary
persistence.** `SkillStore` refuses to persist a record whose declared identity
would place it somewhere other than where it is registered. `save()` does not
acquire relocation semantics.

## Why immutability rather than relocation

Three pieces of repository evidence, none of them about convenience:

1. **No scope-change operation exists.** Nothing in `skillkernel/` implements
   one.
2. **There is nowhere to record one.** `history.yaml` contains exactly one
   list, `transitions`, and every entry is a *maturity* transition. A scope
   change today would be an unrecorded lifecycle mutation — precisely what the
   architecture forbids for maturity, where "a maturity change without a gate
   check and a history entry is the silent state mutation the design forbids".
3. **Scope change is already designed as a future gated operation.**
   Cross-project promotion is planned, and
   `config.promotion.cross_project_min_projects` already exists to gate it.

Giving `save()` filesystem-move semantics would let an ungated, unrecorded,
evidence-free scope change happen through a generic persistence call. That is
the asymmetry this record removes: maturity was guarded, scope was not, though
both live under `classification`.

This establishes no new policy. It applies the existing maturity rule to its
sibling field.

## The invariant

`Layout` is the single computing authority:

```
canonical = layout.relative_skill_path(scope, slug)
```

After any successful persistence operation:

```
declared identity == registered location == physical location
```

`SkillStore` and `doctor` call `Layout` and compare; neither re-derives a path.
`SKILL_SCOPES`, previously defined in both `core/paths.py` and
`skills/model.py`, is now defined once in `core/paths.py` — a location-bearing
fact defined twice is the same class of defect.

## Enforcement boundary

`SkillStore._persist()`, the single write path shared by `create()` and
`save()`. Every mutator routes through it (`update`, `attach_evidence`,
`record_transition`, and the promotion engine's `deprecate`), so one check
covers all of them.

**The check precedes the first write**, so a rejected save leaves the workspace
byte-identical: there is nothing to undo. This is verified against the
filesystem, not against Python objects.

## Why not a generic `Registry.put` guard

The review proposed preventing two ids from owning one path at the registry
level. Evidence from every registry user says no:

| Domain | Path derivation | Id in path? | Collision possible? |
| --- | --- | --- | --- |
| knowledge, evidence, observations | `records/<ID>.yaml` | Yes | Structurally impossible |
| experiments | `definitions/<EXP-ID>/v<N>.yaml` | Yes | Structurally impossible |
| **skills** | `<scope>/<slug>/skill.yaml` | **No** | **Demonstrated** |

Four of five derive their path from the immutable id, so a guard would enforce
nothing. Skills are the sole exception because their path comes from mutable
non-id fields.

A generic guard would also have been actively harmful: **experiments
deliberately re-register the same id at a new path** when `revise()` writes
version N+1. A guard phrased as "an id may not change path" would break a
working, tested feature. A regression test now asserts `revise()` still changes
its registered path, so this boundary cannot be crossed by accident later.

## Pre-existing corruption

`doctor` detects a stored mismatch and reports it as an `ERROR` naming both the
stored and the declared location, plus any location registered to more than one
skill. It **never** repairs, moves, deletes or normalizes anything; a test
asserts the corrupted workspace is byte-identical after a run.

Duplicate ownership is derived from the registry index *before* any record is
read. Read-first ordering would hide the case that matters most: when two skills
share a directory, at least one is unreadable by construction, and an exception
would abort the check before it reported the duplication. This was found by
red-teaming, not by design.

## What is not decided here

Relocation itself. A skill that genuinely needs a new scope — the cross-project
promotion path — requires an explicit, gated, recorded operation that does not
exist yet. Until it does, the honest answer to "how do I move a skill?" is that
you cannot, and that is better than moving one silently.
