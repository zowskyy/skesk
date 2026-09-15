# DEC-0006 — Behaviour fingerprint covers behavioural fields only

**Status:** Accepted, implemented, tested.

## Question

Evidence from an evaluation is only valid while the skill it describes has not
meaningfully changed. What counts as a meaningful change?

## Decision

Classify every top-level skill field by role, and hash only the behavioural
ones.

| Role | Fields | In fingerprint |
| --- | --- | --- |
| behavioral | `purpose`, `applies_when`, `do_not_apply_when`, `activation_rules`, `inputs`, `preconditions`, `procedure`, `success_conditions`, `failure_modes`, `verification` | **Yes** |
| lifecycle | `version`, `classification`, `project_scope`, `confidence`, `deprecation`, `updated_at` | No |
| provenance | `evidence`, `provenance` | No |
| presentation | `name` | No |
| identity | `schema_version`, `id`, `slug` | No |

## Rationale

The fingerprint answers exactly one question: *does this evaluation still
describe this skill?*

Including lifecycle fields creates a circularity. Promotion writes to
`classification.maturity` and `evidence`. If those were covered, recording a
promotion would change the fingerprint and thereby invalidate the evaluation
that justified the promotion — the gate would destroy its own evidence.

`name` was reclassified out of the fingerprint while writing this down.
Renaming a skill cannot change what a consumer following it would do, so it
must not invalidate that skill's evidence. This is what the exercise of
classifying fields, rather than maintaining a list, is for.

## Enforcement

- A test asserts `FIELD_ROLES` covers the schema's fields **exactly**, so
  adding a field without classifying it fails the suite rather than being
  silently excluded from the fingerprint.
- A test per behavioural field asserts changing it changes the fingerprint.
- A test per non-behavioural field asserts changing it does not.
- A test asserts promotion from `candidate` to `trusted` preserves the
  fingerprint — the circularity, checked directly.

## Note

This is deliberately not a blanket exclusion of bookkeeping. Each field was
assigned a role on its own merits, and `content_hash()` still covers the whole
document for detecting any edit at all.
