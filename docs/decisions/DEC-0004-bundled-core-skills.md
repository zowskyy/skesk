# DEC-0004 — Bundled core skills enter at `candidate`

**Status:** Accepted; **arrival maturity amended by DEC-0015**.
Bundled skills now arrive at `observed` and reach `candidate` through the
ordinary gate rather than being granted it. The reasoning below is unchanged
and is what DEC-0015 follows through on.

## Question

At what maturity should skills shipped with the kernel arrive in a repository
that has just been initialized?

## Decision

`candidate`, with provenance `bundled`. Not `validated`.

## Rationale

`validated` means a passing evaluation exists in this repository's evidence
ledger. A bundled skill has none — it arrives with the package. Marking it
validated would manufacture repository-local evidence, which is the precise
failure the whole system exists to prevent.

Maturity communicates confidence, not availability: a `candidate` skill is
readable and usable, it simply has not been shown to work *here*. Running the
evaluator locally produces genuine evidence and promotes it through the normal
gates, which also exercises the lifecycle on real content.

## Consequence

A freshly initialized repository has no validated skills. That is the correct
and intended starting state.

## Open question, deferred

Reuse across repositories may later justify distinguishing `upstream_trusted`
(validated by its authors, elsewhere) from `project_validated` (validated here).
Not implemented, and deliberately not designed yet — the need should be
demonstrated by real cross-project use first.
