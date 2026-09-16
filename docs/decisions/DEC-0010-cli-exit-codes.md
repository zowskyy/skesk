# DEC-0010 — CLI exit-code doctrine

**Status:** Accepted. Recorded **before** any CLI code was written.

## Question

What does each exit status of the `skillkernel` command mean, and in particular
how is "the kernel ran its checks and found a problem" distinguished from "the
kernel itself broke while checking"?

## Decision

```
0   command completed successfully
1   doctor completed and found an ERROR
2   command-line usage error
3   target is not an initialized SkillKernel workspace
70  unexpected internal software failure
```

### The doctrine for 70

> Exit 70 represents an unexpected internal software failure **after command
> parsing**. It must not be emitted for expected domain or validation failures,
> and must not be converted into a `DoctorReport` finding.

Stated deliberately more broadly than `doctor`, so it stays stable as other
commands arrive.

## Rationale

`1` and `70` answer different questions:

| Code | Claim |
| --- | --- |
| `1` | SkillKernel successfully ran its validation machinery and found an integrity failure. **The report is trustworthy.** |
| `70` | SkillKernel itself failed unexpectedly while attempting validation. **The report is not trustworthy, because it is incomplete.** |

Collapsing both into `1` destroys that distinction, and the direction it fails
in is the dangerous one: a crashed validator would be read as a completed
validation that found a problem, when in fact an unknown number of checks never
ran. A repository could then be declared merely "unhealthy" when the truth is
"unknown".

`70` is `EX_SOFTWARE` from BSD `sysexits.h` — an existing external convention
rather than a project-local number invented for this slice. It also sits far
outside the `0`–`3` range, so it can never be mistaken for a finding count or a
validation outcome.

## Implementation constraints

**Catch `Exception`, never `BaseException`.** `KeyboardInterrupt` and
`SystemExit` must retain their normal semantics; a user interrupting the command
has not triggered an internal software failure, and swallowing `SystemExit`
would break the exit path itself.

**Name the validator that raised**, so the diagnostic is actionable.

**No traceback by default.** A traceback is debugging output, not a user-facing
diagnostic. It belongs behind an explicit diagnostic/debug option, which is not
part of this slice.

## Consequence

The `doctor` aggregation boundary handles two distinct classes:

- a `SkillKernelError` is an **expected domain outcome** → an `ERROR` finding,
  exit `1`;
- any other `Exception` is an **implementation fault** → a distinct internal
  error diagnostic, exit `70`.

An unexpected exception must never become a clean report, never exit `0`, and
never be presented as an ordinary validation ERROR.
