# DEC-0012 — Machine-readable doctor output is workspace-independent

**Status:** Accepted, implemented, tested (Vertical Slice 3). Closes a defect
shipped in Vertical Slice 2.

## Problem

`ARCHITECTURE.md` stated that `DoctorReport.to_document()` carries "no
timestamps and no absolute paths, so it is byte-stable across runs **and
comparable across machines**."

The byte-stability half held. The comparability half did not. `Registry.load`
raises an `IntegrityError` whose message embeds an absolute path, and `doctor`
stored that exception text verbatim as a finding message:

```json
{"location": "SKILL-0001",
 "message": "/tmp/tmparsc6ty2/ws/skills/project/n/skill.yaml declares id 'SKILL-0002'…"}
```

VS2's test only exercised a healthy workspace, where no such message is
produced. The claim was shipped untested on the path that could violate it.

## Decision

**The contract stands; the implementation is fixed.** Machine-readable doctor
output is workspace-independent: two equivalent workspaces under different
absolute roots produce identical reports.

The documentation did not overclaim. "Comparable across machines" is
unambiguous, and it is the property that makes a report worth storing, diffing
or attaching to evidence. Weakening the wording to match a defective
implementation would have traded a useful guarantee for a convenient one.

## Where normalization happens

At the **report boundary**, in `doctor` — the component that makes the promise.

Lower-level exceptions keep their absolute paths. An absolute path is exactly
what a developer wants from a traceback; only the machine-readable report
promises independence. A test asserts `Registry.load` still raises with an
absolute path, so this separation cannot erode.

`normalize_workspace_paths()` replaces the known workspace root — in both its
literal and resolved forms — with the token `<workspace>`. It is targeted at the
specific root the report describes, not at anything path-shaped: unrelated
absolute paths such as `/etc/hosts` are left alone, and a root of `/` is never
used for replacement, since that would mangle every path in the message.

Normalization is applied centrally, as findings are added, so no call site can
forget it.

## Kept separate from DEC-0011

The two defects were discovered in the same review but are unrelated: one is a
storage-integrity failure, the other an output-serialization failure. They are
implemented, tested and described independently so that either can be traced,
reverted or revisited without disturbing the other.
