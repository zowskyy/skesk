# DEC-0021 — A suite is exactly the cases its definition names

**Status:** Accepted, implemented, tested (Vertical Slice 8).
**Extends:** DEC-0018 (no adoption), DEC-0019 (evaluation-input identity).
**Relates to:** DEC-0013, DEC-0017, DEC-0009.

## Problem

An evaluation suite was whatever the two `examples/` directories happened to
contain. Three consequences, all measured at the frozen VS7 checkpoint
`db11600`.

**Presence conferred participation.** A file dropped into
`examples/positive/` was loaded, scored and folded into
`evaluation_input_digest` — with no author, no review and no record that it was
ever meant to be part of the corpus. This is the mirror image of the rule
DEC-0018 states for ownership: there, physical state was refused the right to
*be* logical state; here, physical state was silently granted it.

**Replacement had no commit point.** `write_evaluation_suite` wrote the new
cases, retired the old ones, then wrote the definition. Two interruption
windows were reproduced:

| interrupted | a reader saw |
| --- | --- |
| before retirement | `corpus-a` metadata over `a-one, a-neg, b-one, b-neg` — the union |
| after retirement, before the definition | `corpus-a` metadata over `b-one, b-neg` — B under A's label |

Both states load cleanly and evaluate. Evidence recorded from either one is a
claim about a corpus that never existed.

**Nothing could be reported.** With no record of which files a suite was
supposed to read, a case saved as `.yml`, or filed one directory too deep, was
indistinguishable from a file that was never meant to count. `doctor` had
nothing to compare against and said nothing.

## Decision

> A suite comprises exactly the canonical direct `.yaml` case files its
> definition names, and the atomic write of that definition is the only
> transition that changes what a reader sees.

### The definition names its cases

`schema_version: 2` of the evaluation definition carries a `cases` manifest:

```yaml
cases:
  - examples/positive/a-one.yaml
  - examples/negative/a-neg.yaml
```

Entries are relative to the *skill* directory, so moving a skill between scopes
does not rewrite its suite. Each entry is checked as a string, against a grammar
admitting exactly one shape — `examples/<positive|negative>/<case-id>.yaml`,
with a canonical case id (DEC-0013) — before any path is built from it, and the
resolved path is then proved against the directory that owns it. That is
DEC-0017's two layers, applied to a document a person can edit: a traversal, an
absolute path, a nested directory and a second file extension are all
unrepresentable, and a symlink, which no grammar sees through, is caught by
containment.

The manifest is a **set**. Cases load in canonical order — positive before
negative, then by file name — whatever order the manifest lists them in, so
reordering it cannot move a digest or a score. A path listed twice is refused.

Nested cases are deliberately **not** supported. Adding depth would reopen the
question this decision closes, in a place harder to see.

### Named, and therefore required

A named case that is missing, unreadable, schema-invalid or not a regular file
**refuses the whole suite**. It is not a smaller corpus: a definition that names
five cases and scores four is measuring something nobody authored. The failure
is loud, and `current_input_digest` already treats an uncomputable digest as
*not current* rather than as an absence of evidence.

### Writing is committed, in four steps

0. Validate the entire new corpus, writing nothing, so a malformed case refuses
   before a byte of the repository has changed.
1. Upgrade a legacy definition in place (below).
2. Write every case of the new corpus **while the definition still names the old
   one**.
3. Write the definition naming the new corpus. **This is the commit.**
4. Retire the case files the new definition no longer names.

Before step 3 a reader sees exactly the old suite; after it, exactly the new one.
There is no instant at which a reader sees the union, or sees the new cases under
the old corpus's metadata. Step 4 is housekeeping: by then the retired files are
already outside the suite, so an interruption there changes no answer — it leaves
files that `doctor` reports and that re-running removes. Re-running after an
interruption at any step converges on the same result.

**One honest limit.** Replacement is atomic in *membership*, not in *content*. A
case path both corpora name is one file, overwritten in place before the commit,
so during the window a reader sees the old set carrying the new content for that
case. Making that atomic too would mean staging every case and renaming a
directory, which is a larger change than this slice takes on. It is asserted as a
test rather than left implied.

### Schema v1 still reads, and upgrading it is invisible

A `schema_version: 1` definition has no manifest and keeps discovering its cases
by globbing, so an existing workspace reads unchanged. It is never written again:
the first authoring pass over such a suite upgrades it, as its own atomic write,
naming exactly the direct case files the legacy loader sees at that moment. Same
files, same cases, same `evaluation_input_digest`, same `corpus_content_digest`,
same evidence still current. Interrupted, the definition is still the legacy one
and the suite still reads.

The upgrade runs *first* in an authoring pass, and must: a legacy definition
names nothing, so left in place it would adopt the new cases as they landed —
which is the defect, not a step towards fixing it.

A v1 document carrying a `cases` key is refused rather than read either way,
because read as declared the manifest would be ignored and the document would be
saying two different things.

**The one case that refuses.** `validate_case_id` is a write-side rule, so a
pre-grammar workspace can hold a direct case file whose name a manifest cannot
express. Dropping it would silently change what the suite measures, so the
upgrade stops and names the file. Renaming it is the author's call, not the
kernel's.

### Identity covers inputs, not encoding

`schema_version` and `cases` are excluded from the definition's contribution to
`evaluation_input_digest`. One says how to read the document and the other says
which files to read; neither is itself an input, and excluding them is what makes
the upgrade a no-op for evidence.

They are not a hole in coverage. Every consumed file is framed into the digest by
its own path, so a manifest naming a different set moves the digest *through the
files*. What is deliberately not covered is a future version that reinterprets a
value this one already reads — that would need its own migration, not a silent
bump.

**Migration cost, stated plainly.** Removing `schema_version` from that field set
changes the digest of every definition, so evaluation evidence recorded by VS6 or
VS7 no longer matches the inputs on disk. `doctor` reports those skills as
`evaluation-input:mismatch` and re-evaluating clears it; the earlier evidence
stays as history, and `corpus_content_digest` — what `trusted` counts — is
unaffected, so no accumulated corpus distinctness is lost.

### Unnamed case-shaped content is reported, not read

`doctor` reports a `.yaml` or `.yml` entry anywhere under a skill's `examples/`
tree that the suite does not name, as a **WARNING**, at every maturity.

WARNING and not ERROR because an unnamed file is simply not read: it cannot
corrupt an evaluation, move a digest or affect a promotion. The finding is about
a person's expectation — a case someone believes is being scored and is not — and
about the leftovers of an interrupted authoring pass. `.yml` is included
precisely *because* it is not a case file: that near-miss is the reason to look.

Everything else in the tree — notes, drafts, diagrams, subdirectories — is
authoring material and is left alone and unmentioned. **`doctor` is not a
filesystem linter.** When the definition cannot be read there is no manifest and
so no answer to "which files belong here"; this check stays silent and
`evaluation-input` reports the readability failure, rather than burying it under
guesses.

Reporting keys on the entry's own name, never on what it resolves to, so a
symlink beside a named case is the second entry it is.

## What this does not change

DEC-0019 is intact. `evaluation_input_digest` still identifies the live input
state keyed by path, `corpus_content_digest` still identifies the case set keyed
by `case_id`, snapshots are still written once and never rewritten, and
`trusted` still counts distinct corpora proved from preserved inputs. DEC-0020's
artifact ownership is untouched. Retirement remains bounded to direct `*.yaml`
files in the two managed polarity directories and is still the only deletion the
kernel performs.

The case document format is unchanged, and is now versioned separately: the two
documents change for different reasons, and sharing one constant meant bumping
the definition would have invalidated every case file ever written.

No repair, migration command, adoption or cleanup operation is introduced. An
unnamed file stays where it is until a person moves it.
