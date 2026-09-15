# DEC-0014 — A bundle is a portable definition, and source provenance is an extension field

**Status:** Accepted, implemented, tested (Vertical Slice 4).
**Relates to:** DEC-0006 (behaviour fingerprint), DEC-0011, DEC-0013.

## Problem

A skill proven in one repository cannot be delivered to another. Every skill has
to be authored by hand where it is used, which is the duplication this project
exists to remove.

The obvious implementation — serialize a `SkillRecord` and read it back
somewhere else — is exactly wrong. A record carries an identifier allocated by
one workspace's registry, a maturity earned through that workspace's gates,
evidence references into that workspace's ledger, a promotion history, and
timestamps. Shipping any of it would let a skill arrive somewhere new already
holding confidence it never earned there. That is the failure this system is
built to prevent, arriving through the front door.

## Decision

A **bundle** carries the *definition* and nothing else: name, slug, scope,
purpose, activation boundaries, procedure, success conditions, failure modes,
verification, and a static evaluation definition (cases, thresholds, corpus id).

Everything else is workspace-owned and is refused by name, not merely left
unmentioned — `id`, `classification`, `maturity`, `confidence`, `evidence`,
`provenance`, `history`, `project_scope`, `deprecation`, `created_at`,
`updated_at`, `version`. A manifest containing one of them is rejected with that
key named, so a mistake is never silently dropped.

The bundle schema's root is `unknown="reject"`. A bundle is external input; an
unknown key there is far more likely to be an error or an injection attempt than
a forward-compatible extension.

## The fields the bundle contributes

Exactly the ten fields `SkillStore.EDITABLE_FIELDS` already admits, minus
`confidence` (which is a workspace judgement, not a property of the text). The
installer asserts this correspondence at preflight: if the bundle format ever
carries a field the skill store considers kernel-owned, that is a bug in this
repository and it fails loudly rather than writing it.

Those ten are also, by DEC-0006, the behavioural fields the fingerprint covers.
So "what a bundle can carry" and "what changes a skill's behaviour" are the same
set, which is the property that makes a portable definition meaningful.

## Source provenance: `provenance.x_source`

An installed skill records where it came from, in three fields:

```yaml
provenance:
  created_by: bundled
  created_from: ["bundle:two-method-escalation@1.0.0"]
  x_source:
    bundle_id: two-method-escalation     # is this the same portable skill?
    bundle_version: "1.0.0"              # which declared release?
    content_hash: "sha256:…"             # exactly which bytes?
```

Each answers a distinct question, and the block is validated by an owned spec
with `unknown="reject"` — a fourth key is unaudited state entering through the
one door the schema leaves open, so it is refused.

### Why an extension field rather than a schema change

Today bundles are the only external source. A first-class `source` field on
`SKILL_SCHEMA` would be a guess about every future source type, taken with a
sample size of one, and paid for with a schema version bump and a migration for
every existing record.

`provenance` already declares `unknown="allow_extension"`, so an `x_`-prefixed
key is the schema's own supported answer to "this is real but not yet
universal". Nothing about it is a placeholder: it is written by one authority,
validated against an owned spec, and checked by `doctor` on read.

**The criterion for promoting it to a first-class field**, stated now so the
decision is not left to whoever next touches the file: a *second* source type
exists, or a lifecycle requirement (a gate, a promotion rule, a compiled output)
needs source provenance from a record that has no bundle. Until one of those is
true, the extension field is the honest shape.

## Read-side enforcement

The installer being careful proves nothing on its own: `skill.yaml` is a plain
file a person can edit. `doctor` therefore validates any `x_source` it finds
against the same spec, and reports a record claiming a shape the installer would
never have produced. A skill with no `x_source` is not a finding — most skills
are authored locally and have no external source to declare.

## What a bundle deliberately cannot ship

No `EV-` records, no observed scores, no fingerprints, no experiment references,
no promotion evidence, and no `eval.yaml` — the suite definition embeds the
workspace-local `SKILL-*` identifier, which a bundle does not have and must not
invent. The bundle carries cases and thresholds; the workspace's own
`write_evaluation_suite()` binds them to the identity it allocated.
