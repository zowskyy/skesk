# DEC-0017 — A destination is contained by the root that owns it

**Status:** Accepted, implemented, tested (post-VS4 correctness repair).
**Extends:** DEC-0013. **Relates to:** DEC-0011.

## Problem

DEC-0013 established that a value used as a path *component* must satisfy a
canonical grammar, and applied it to the skill slug. That is one half of path
safety. The other half went unstated, and two defects found after the VS4 freeze
both lived in it.

The pattern, in both cases:

```
directory = require_inside(parent)          # the PARENT is contained
destination = directory / untrusted         # a component is appended
write(destination)                          # nothing re-checks the RESULT
```

Containment of a parent says nothing about a child appended afterwards. A guard
placed before the join is not a guard on what is written.

**Two reproductions, both through ordinary public APIs.**

*Evaluation cases.* `case_id` was typed only as a non-empty string. The writer
joined it onto an examples directory it had already contained. An absolute id
discarded the directory entirely; a traversal id climbed out of it. Measured at
`a402432`: a packaged bundle that passed every validation wrote outside the
workspace and **overwrote a pre-existing file**.

*Registry records.* `Registry.resolve` joined an index-supplied relative path
onto a domain directory and asked only whether the result was inside the
repository. `knowledge/../skills/core/x.yaml` is inside the repository and
outside the knowledge domain. Measured: a knowledge record written into
`skills/core/`, with `doctor` reporting no error.

`Layout.require_inside` answers exactly one question — *is this inside the
repository?* Its docstring claimed "every write the kernel performs goes through
this", and both defects falsified that claim. It was never the wrong check; it
was the wrong *boundary* for these writes.

## Decision

Path safety has two layers, and both are required. Neither substitutes for the
other.

**Layer A — the component.** Every value that becomes a path component is
validated against the canonical grammar *before* the path is constructed. This
is DEC-0013, generalized: one grammar, one length bound, one authority
(`validate_component` in `Layout`'s module), reached through named wrappers such
as `validate_slug` and `validate_case_id`. The dangerous path is never built.

**Layer B — the destination.** Every write is contained by the root that *owns*
it, asserted on the final resolved path. `Layout.require_within(owner, path)`
expresses this. It checks repository containment first, so the outermost violated
boundary is the one reported, then requires the destination to be at or beneath
the owning root.

Applied where a boundary that is narrower than the repository actually exists:

| Writer | Owning root |
| --- | --- |
| evaluation case files | that skill's `examples/<polarity>/` |
| registry records | that domain's directory |

## Why both, when either looks sufficient

Layer A alone is a single point of failure: it depends on every present and
future caller remembering to validate. `write_evaluation_suite` is public, and
the installer was not its only possible caller.

Layer B alone is weaker in kind. Joining arbitrary input and inspecting the
result means the dangerous path gets constructed before it is judged — the
objection DEC-0013 already raised, and the reason it preferred a grammar. It also
cannot distinguish "escapes" from "merely absurd": an identifier 300 characters
long stays inside its directory and still fails at the filesystem, *after*
earlier writes have landed, converting a refusal into partial state.

So Layer A makes the escape unconstructible and Layer B makes it undetectable-by
-omission. A defect has to defeat both.

## The length bound

`MAX_COMPONENT_LENGTH = 128`. Shape alone does not make a component writable:
filesystems cap a single name near 255 bytes and the kernel appends suffixes. An
unbounded but grammar-valid identifier therefore fails at the write with a bare
`OSError` rather than a domain refusal — found in the red-team pass, where a
300-character case id left a registered skill with no evaluation suite.

128 is conservative rather than derived from any one filesystem: comfortably
under every limit this runs on, and roughly four times the longest identifier the
repository has ever used. The bound exists so that a refusal stays a refusal.

## Scope, and what this does not change

**DEC-0013 is not rewritten.** Its grammar, its reasoning and its conclusion
stand exactly as written; this records the second layer it did not address, and
generalizes its authority from "skill slug" to "path component" without changing
what that authority accepts.

**Validation is on the write path only.** `load_evaluation_suite` discovers cases
by globbing and never joins an id onto a path, so a workspace holding a legacy
non-canonical identifier still reads. Making the grammar a *read* precondition
would break existing workspaces to no benefit.

**`require_inside` keeps its job.** It is still the single implementation of
repository containment, and `require_within` composes with it rather than
competing. There is no second filesystem-boundary implementation.

## The rule, for anything written later

> A value that becomes a path component is validated against the canonical
> grammar before the path is built, and the resulting destination is proved to
> be inside the root that owns it.

Seven identifiers already satisfied this when the rule was written — slug, scope,
record ids, experiment version and run number, evidence artifact name, bundle id.
`EvidenceLedger._artifact_destination` had been practising it since before the
rule existed. `case_id` and `Registry.resolve` were the two that did not, which
is why this is recorded as a rule rather than left as two bug fixes.
