# DEC-0019 — An evaluation identifies the inputs it was run against

**Status:** Accepted, implemented, tested (Vertical Slice 6).
**Amends:** DEC-0009 (what "distinct corpora" means).
**Relates to:** DEC-0003, DEC-0006, DEC-0007, DEC-0016.

## Problem

The runner stamped every evaluation with the skill's behaviour fingerprint, so
the `validated` gate could ask *is this evaluation still about this skill?*
There was no matching question about the corpus. `corpus_id` was a free string in
a plain file, and nothing recorded what an evaluation had actually consumed.

Measured at `68eee05`: deleting every negative case — the guardrail DEC-0003
calls the dangerous direction — left the passing evidence counting toward
promotion, with `doctor` silent. Deleting `scorer/eval.yaml` entirely did the
same. A skill's standing rested on evidence about a corpus that no longer
existed, and nothing in the system could tell.

Closing that exposed a second defect underneath. `trusted` requires passing
evaluations across N distinct corpora, but a skill has **one** live suite, so
requiring every counted evaluation to match it made the requirement
unsatisfiable. It had been satisfiable only *because* stale evidence counted.
Worse, `write_evaluation_suite` never removed anything: authoring corpus B left
A's cases in place, so the suite labelled B loaded as A ∪ B. The second
"distinct corpus" was measured to score the union of both case sets.

## Decision — three concepts, deliberately separate

### 1. Live evaluation-input identity — `evaluation_input_digest`

Every consumed file, keyed by its repository-relative path, over scoring **and**
provenance fields. Answers *are these the inputs on disk now?*, and is what
`validated`'s `current_only` requires alongside the fingerprint.

An identity claim, not a model of scoring semantics. Hashing parsed content makes
indentation, quoting, key order and line endings irrelevant, but **no
value-level normalization is applied**: a reordered `signals` list changes the
digest even though `applies_to` builds a set and would score it identically.
Re-implementing the scorer's notion of equivalence here would put the same
semantics in two places, and the costs are lopsided — a false "not current"
costs one deterministic re-run, a false "current" costs a wrong promotion.

`description` is excluded from both digests: documentation carried beside the
inputs, reaching no consumer. Unknown `x_` extensions are included on both suite
and case, because an extension could carry meaning this version does not know
about.

### 2. The immutable snapshot

Every evaluation writes the exact parsed inputs it consumed into its own evidence
artifact, beside the report. That artifact is already hashed, size-checked,
chain-linked and orphan-detected, so preserving the inputs inside it makes them
immutable and independently verifiable **without inventing a second artifact
store**. It is what lets historical evidence stay checkable after the live suite
has moved on.

### 3. Corpus content identity — `corpus_content_digest`

The case set alone, keyed by `case_id`, over scoring content only. The definition
is excluded entirely: a corpus is its cases. Retuning a threshold, bumping
`scorer_version`, rewriting `corpus_id` or re-filing the same cases under new
filenames therefore all leave corpus identity unchanged, while adding, removing
or editing a case does not.

## Two gates, two questions

`validated` asks about **now**: evidence for this skill, against the inputs on
disk today. `trusted` asks about **accumulated history**: has this been
independently confirmed across genuinely distinct corpora. They must not share a
notion of currentness, or the second is unanswerable by construction.

An evaluation counts toward `trusted` only when its verdict passed, its
fingerprint still matches, its snapshot exists, the ledger's artifact
verification passes, the snapshot parses, and the corpus digest **re-derived
from those preserved inputs** equals the digest the record claims. A recorded
digest string is a claim; the preserved inputs are the proof. Both checks are
load-bearing and independently tested — mutation testing found the artifact-hash
check unpinned until a tamper that leaves corpus content intact was added.

## One parsed representation

`EvaluationInputs` is read once and everything derives from it: scoring
execution, both digests, and the snapshot. Nothing re-reads the filesystem
afterwards, so the four cannot disagree about what was evaluated.
`EvaluationInputs.from_snapshot` rebuilds the same structure for verification, so
a historical evaluation is checked by exactly the code that produced it.

## Authoring a corpus now yields that corpus

`write_evaluation_suite` removes managed case files the authoring pass did not
write. Deliberately narrow: direct `*.yaml` files in the two managed polarity
directories only, never recursively, leaving nested directories, non-YAML files
and every other deferred question untouched. New files are written first, so an
interruption leaves the old merge behaviour rather than a suite with cases
missing. Each removal is proved inside the directory that owns it (DEC-0017).
This is the only deletion the kernel performs.

## Legacy

Evidence recorded before VS6 has no digest and no snapshot. It remains immutable,
enumerable history and is never rewritten, backfilled or inferred from the
current filesystem — that would claim knowledge never recorded. It cannot satisfy
`current_only` and cannot count toward `trusted`. Re-evaluation is the only
remedy, and it supersedes rather than replaces: `doctor` is clean again with the
old records untouched beside the new one.

## What this does not do

No generic provenance framework. No change to bundle hashing (DEC-0016 is
corrected in wording only). No new maturity state, no repair or migration
command, and no change to the nested-suite enumeration, which remains deferred.
