# DEC-0005 — `skill.yaml` is authoritative; `SKILL.md` is generated

**Status:** Accepted. Generation not implemented yet.

## Decision

`skill.yaml` holds the structured truth. `SKILL.md` is a generated,
human-readable projection of it, carrying a header declaring that it is
generated and naming its source.

## Rationale

A hand-written `SKILL.md` beside a hand-maintained `skill.yaml` gives every
fact two mutable homes. They drift, and nothing can say which is right. One
authoritative location per fact is a stated invariant of this project, and this
is the place it would be easiest to violate.

The same pattern is already implemented for the project profile:
`docs/project/profile.yaml` is authoritative and `docs/project/PROFILE.md` is
generated with a `<!-- GENERATED FILE - do not edit -->` header.

## Required behaviour when implemented

Generation must be deterministic — same input, byte-identical output — so
staleness can be detected by regenerating and comparing.

Integrity checking must detect:

- a missing generated document;
- a stale generated document (source changed, projection not regenerated);
- a manually modified generated document.

All three reduce to the same comparison, which is why determinism is a
requirement and not a nicety.
