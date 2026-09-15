# DEC-0007 — The ledger provides tamper evidence, not immutability

**Status:** Accepted. Terminology corrected in Milestone 0.

## Problem

The evidence ledger originally described itself as "append-only". That
overclaims, and promotion decisions are going to rest on this component.

## The actual guarantee

> Unauthorized modification of ledger history becomes **detectable** by
> `verify()` under the current trust model.

## What this does and does not mean

Each record carries the identifier and hash of its predecessor. Modifying,
deleting, inserting or reordering a record breaks the chain from that point on,
and `verify()` reports it. Artifacts are hashed, so content changes are caught
too.

It does **not** mean the history cannot be rewritten. Anyone who can write to
the repository can edit a record and recompute every subsequent hash, producing
a ledger that verifies cleanly. This is asserted by a test —
`test_a_fully_recomputed_chain_still_verifies` — so the limit is documented in
executable form rather than only in prose.

What the chain actually buys: the cost of a silent edit rises from changing one
line to rewriting all remaining history, and the ordinary accident
(hand-editing a record, restoring a stale file, deleting an artifact) becomes
loud instead of silent.

## Real immutability

Would require an authority outside the repository — signed commits, an
append-only remote, or external timestamping. Out of scope for now, and it
should be an explicit decision when it is taken, not an assumption inherited
from a word in a docstring.

## Rule going forward

Documentation states the guarantee a component actually provides. "Append-only"
in the absolute filesystem sense is not claimed anywhere.
