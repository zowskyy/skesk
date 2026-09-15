# DEC-0002 — Package structure deviations

**Status:** Accepted.

## Decision

Deviate from the directive's proposed tree in six places, and record each.

| Deviation | Reason |
| --- | --- |
| Added `skillkernel/skills/` | The skill contract and maturity machine are the central domain object. Placing them inside the generic `registry` package would hide the system's subject matter inside its storage layer. |
| Observations in `skillkernel/discovery/` | Discovery is their only consumer; a separate package would be one module deep and pull them away from the code that reads them. |
| Decisions to live in `skillkernel/project/` | Decision records are project policy, alongside the profile. |
| Added `evidence/records/` | Evidence records need somewhere to live; the directive's tree named only `registry/` and `artifacts/`. |
| Added `observations/` at the repository root | Observations are first-class records, peers of knowledge and experiments. |
| Added `skillkernel.yaml` at the root | Kernel policy (thresholds, gates) must not be mixed with project facts, or a threshold change reads as a change in what the project is. The file doubles as the repository-root marker. |

A planned `skillkernel/compiler/` package is a seventh deviation; it is not
created until the compiler is written.

## Note

Empty directories are not created in advance. A directory that exists but
contains nothing asserts a capability that is not there, and the first
completion report in this project did exactly that.
