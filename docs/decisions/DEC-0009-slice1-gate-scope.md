# DEC-0009 — Promotion gates check fields and evidence, not generated documents

**Status:** Accepted. Narrowing applied in Vertical Slice 1.

## Question

`ARCHITECTURE.md` described `SKILL.md` as an artifact required at
`experimental`, alongside `CHANGELOG.md` and example directories at `validated`.
Should Slice 1's gates enforce those file requirements?

## Decision

No. Slice 1 gates on **record fields and evidence only**:

| Target | Gate checks |
| --- | --- |
| `candidate` | purpose, `applies_when`, `do_not_apply_when`, provenance |
| `experimental` | procedure; a frozen experiment with a recorded result |
| `validated` | success conditions, failure modes, verification; a passing evaluation whose fingerprint matches the skill |
| `trusted` | passing evaluations across N distinct corpora (see DEC-0019) |
| `deprecated` | a stated reason |

Refuted knowledge disqualifies at every maturity.

## Rationale

`SKILL.md` is a *generated* projection of `skill.yaml` (DEC-0005), and the
generator does not exist. A gate requiring it would have one of two outcomes,
both bad: it forces the generator to be built before anything demands it, or it
is satisfied by a hand-written file — which is precisely the two-mutable-homes
problem DEC-0005 exists to prevent.

The evidence requirements are the load-bearing half anyway. A skill with a
passing evaluation and no `SKILL.md` is under-documented. A skill with a
beautiful `SKILL.md` and no evaluation is unfounded. Slice 1 gates the second.

## What this does not weaken

The `validated` gate is *stronger* than originally sketched: it requires the
passing evaluation's `skill_fingerprint` to match the skill's current
fingerprint, so an evaluation of a procedure that has since been rewritten does
not count. That constraint was not in the original design and emerged from
building the slice.

## When this is revisited

When the `SKILL.md` generator lands. Document requirements then become
checkable by regeneration and comparison, which is a real check rather than a
file-exists check. Until then the gates claim only what they verify.

## Amendment — DEC-0019

"Distinct corpora" means distinct **verifiable scoring content**, not distinct
`corpus_id` strings. A label was never evidence of distinctness: VS6 measured a
suite labelled `corpus-b` whose content was `corpus-a ∪ corpus-b`, and nothing
detected it.

An evaluation contributes to `trusted` only if its preserved input snapshot still
verifies and re-derives the corpus digest it claims. The requirement itself is
unchanged and not weakened — repeated evidence from one corpus is still
repetition rather than independent confirmation. What changed is that the
requirement can now be checked.
