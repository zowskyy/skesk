# DEC-0001 — Hand-written schema engine

**Status:** Accepted, provisionally. Revisit if the maintenance cost rises.
**Date:** Milestone 0.

## Question

Should record validation use `jsonschema`, `pydantic`, or an engine written for
this project?

## Decision

Write one, in `skillkernel/core/schema.py` (~400 lines), and accept the
resulting testing burden as the price.

## Rationale

The kernel needs three things, and the first is the one that decided it:

1. **A per-object unknown-field policy.** Records must support
   forward-compatible `x_`-prefixed extensions while rejecting every other
   unrecognised key, and that policy must be settable *per object* — strict for
   the skill contract, free-form for an environment fingerprint. Both libraries
   can be coerced into this; neither makes it the natural expression.
2. Dotted error paths suitable for CLI diagnostics, with all violations
   reported together.
3. No runtime dependency beyond PyYAML, since the kernel is meant to be
   embedded in arbitrary repositories.

## Cost accepted

Substituting for a mature library means the engine must be tested as if it were
one. 68 tests cover it, weighted toward adversarial cases: type confusion
(including `bool`/`int`, which matters because `bool` subclasses `int` in
Python), nested paths, list index paths, all three unknown-field policies,
schema versioning, non-mapping roots, multiple simultaneous errors, and a
malformed spec failing loudly rather than validating everything.

## Reassessment

After writing those tests, the engine remains simpler than the adapter layer
either library would need in order to express the unknown-field policy, and it
has no defects outstanding. The decision stands.

**Revisit if:** validation defects are found in use; the engine grows past
roughly 600 lines; or a requirement appears (JSON Schema interop, code
generation, serialisation) that a library provides and this does not.

**Explicitly not a reason to revisit:** that a third-party library exists.
