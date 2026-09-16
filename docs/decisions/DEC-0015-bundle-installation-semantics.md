# DEC-0015 — Bundle installation semantics

**Status:** Accepted, implemented, tested (Vertical Slice 4).
**Amends:** DEC-0004, DEC-0008.

## Decision

`skillkernel skill install <bundle-id>` creates a new, ordinary, workspace-local
skill from a packaged definition. The workspace allocates the identifier, the
skill starts at `observed`, and every collision is refused.

## Amendment to DEC-0004 — arrival maturity is `observed`, not `candidate`

DEC-0004 decided that bundled skills enter at `candidate`. The implementation
enters at `observed` and reaches `candidate` by passing the ordinary gate.

This is a strengthening, not a reversal. DEC-0004's reasoning was that
`validated` would manufacture repository-local evidence; granting `candidate` on
arrival would have been a smaller version of the same move — a maturity handed
out because of where a skill came from rather than what it satisfies.

Verified against the real gates, with no special-casing:

```
after install             maturity = observed
observed -> candidate     ALLOWED    (the gate is satisfied by the definition alone)
evaluate at candidate     verdict = pass, evidence = EV-0001 (allocated locally)
candidate -> experimental REFUSED    "cites no experiment evidence"
```

So DEC-0004's intended outcome still holds — a bundled skill is readable and
usable at `candidate` almost immediately — but it now *earns* that maturity
through the same gate every other skill passes. The candidate gate asks for
purpose, `applies_when`, `do_not_apply_when` and `provenance.created_from`, all
of which a bundle legitimately supplies.

`experimental` is correctly out of reach: it requires a frozen experiment with a
recorded result, which a bundle must never ship. **The honest endpoint for a
freshly installed bundle is `candidate` plus locally-earned evaluation
evidence.**

## Amendment to DEC-0008 — installation is explicit, not part of `init`

DEC-0008 proposed that `skillkernel init` install the bundled core skills.
Installation is instead a separate, explicit command.

Three reasons, in order of weight:

1. **`init` must stay refusable and inspectable.** It creates a workspace and
   says what it created. Silently populating it with skills makes the starting
   state depend on which version of the package happened to be installed.
2. **Installation can fail for reasons that are not initialization's business** —
   a collision, an integrity mismatch, an unmanaged directory in the way. Those
   refusals belong to a command whose whole job is installing.
3. **A workspace should be able to decline.** Not every project wants every
   bundled skill, and "delete it afterwards" is a worse answer than "do not
   install it".

DEC-0008's substance is unaffected: the universal agent policy ships as a
bundled core skill, at `core` scope, with `created_by: bundled`, carrying the
shape that record required. It is now installable, which is the gap DEC-0008
recorded as open.

## Refusal, never reconciliation

| Situation | Behaviour |
| --- | --- |
| Same `bundle_id` already installed | Refuse, naming the existing `SKILL-*` |
| Same `bundle_id` at a different version or hash | Refuse — upgrade is deferred |
| Different `bundle_id`, identical `content_hash` | Refuse — aliasing makes provenance ambiguous |
| `(scope, slug)` held by an unrelated local skill | Refuse |
| A directory on disk that no skill owns | Refuse — residue is never written into |

There is no update, no merge, no rename and no overwrite. Each of those is a way
for imported content to displace content a workspace earned, and none of them
has an outcome a user could predict from the command they typed.

Collision lookup scans installed skills' `provenance.x_source` through
`SkillStore.all()`. **There is no second registry.** A parallel index of
"installed bundles" would be a second mutable home for a fact the skill records
already hold, and the two would drift.

## Zero residue on refusal

Every bundle- and policy-related check runs before the first byte is written,
including a full dry run: the installer builds the exact record it intends to
create, under a placeholder identifier, and validates it. Only after that does
it call `SkillStore.create()`, whose first write is `allocate_id()`.

A rejected install therefore burns nothing — not a directory, not a file, and
not an identifier. Every refusal test asserts this against a filesystem
fingerprint taken before the attempt, with an explicit guard against the vacuous
comparison of an empty tree against an empty tree.

After preflight the only remaining failure mode is genuine I/O interruption,
which is identical to the failure mode every other caller of
`SkillStore.create()` already has. The installer adds no new partial-state risk
and needs no transaction machinery of its own.

## Reuses, does not reimplement

Identifier allocation, record persistence, the location invariant (DEC-0011),
slug validation (DEC-0013), history, the promotion gates and
`write_evaluation_suite()` are all the existing authorities, called unchanged.
The installer contributes preflight and refusal policy; the CLI contributes
argument parsing, rendering and an exit code (DEC-0010). Neither computes a
path, holds lifecycle policy, or writes a file of its own.

## Known limitation, reported not fixed

`Registry.orphan_record_files()` scans `<domain>/records/`, which does not exist
for skills — a skill lives in `<scope>/<slug>/`. So an orphaned *skill*
directory, left by a genuine I/O interruption, is invisible to `doctor`.

The safety half of the documented guarantee holds: readers resolve through the
index, so a partial skill is never readable. The detectability half is
overstated for skills. This is a general `doctor` limitation, not a bundle
concern, and fixing it inside this slice would hide a storage-layer question
inside a feature. Recorded here as an independent candidate.

The installer does mitigate the consequence: it refuses to install into a
directory that exists but that no skill owns, rather than writing into it.
