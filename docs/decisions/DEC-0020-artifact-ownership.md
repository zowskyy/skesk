# DEC-0020 — An evidence artifact is owned by the record that declares it

**Status:** Accepted, implemented, tested (Vertical Slice 7).
**Extends:** DEC-0017 (two-layer path safety), DEC-0018 (no adoption).
**Relates to:** DEC-0007, DEC-0019.

## Problem

`evidence/artifacts/` is the one namespace holding bytes that evidence vouches
for. Three gaps, all measured at the frozen VS6 checkpoint `af0caa0`.

**The write adopted.** `record()` allocated an identifier and then wrote its
artifact with no check that the destination was free. A pre-existing
`evidence/artifacts/<EV-ID>/` was silently overwritten — and `doctor` had been
reporting that very directory as a stray-artifact ERROR until the write cleared
the finding by absorbing it. The same shape DEC-0018 closed for records and
DEC-0011 for skills, at the one surface neither reached.

**The read wandered.** Both artifact readers joined `layout.root` to the stored
path with no containment. A record hand-edited to cite `../../outside/secret.txt`
was opened, hashed and *size-reported in a finding* before the hash comparison
refused it. The refusal was correct; reaching outside the repository to produce
it was not, and the finding disclosed the existence and size of an arbitrary host
file. One of the two sites was introduced by VS6 itself.

**The namespace was open.** The schema allows zero or one artifact per record,
but nothing checked that the directory contained only that. Extra files, nested
directories, symlinks, and a directory belonging to a record declaring no
artifact at all were invisible to `doctor` and to `EvidenceLedger.verify`.

## Decision

> An evidence artifact is owned exclusively by the record that declares it.
> Writes never adopt pre-existing artifact state; reads never leave that
> record's own artifact directory; and the managed artifact namespace contains
> no unowned content.

### Writes claim before allocating

`Registry.allocate_id` already refused a destination derived from the
identifier it was about to issue (DEC-0018). An artifact directory is derived
from that identifier too, so it is claimed in the same breath, through
`also_claims`. The refusal therefore precedes the artifact write, the record,
the index and the counter: nothing is written and no identifier is burned.

The claim is made **whether or not an artifact is supplied**. A record declaring
none owns an empty namespace, so a directory already sitting there belongs to
nobody and is refused rather than quietly inherited.

Checking after allocation was rejected: it would burn an identifier for a write
that cannot happen, which is the partial state the zero-write contract exists to
prevent.

### Reads are bounded lexically, before any access

`resolve_declared_artifact` is the single authority. It checks the declared path
**as a string first** — exactly `evidence/artifacts/<this record>/<one plain
name>` — so a corrupt record cannot make the kernel open, stat, hash or size any
path outside that directory. Only a path surviving that is resolved, and then
`Layout.require_within` proves it against the owning directory (DEC-0017 Layer
B). Verification and the promotion gate share the one function, so they cannot
drift into disagreeing about the boundary.

Tests assert this with detonators on `stat`, `open`, `read_bytes` and friends
targeting the exact declared path, because "a later hash check refuses it" is a
different and weaker claim than "it was never touched".

The write side gained Layer B for symmetry: `Path(name).name` already made an
escape unconstructible, and the destination is now proved against the owning
directory as well, so neither layer stands alone.

### The namespace is closed

For each registered record: a declared artifact means its directory holds
**exactly** that one regular file; no declared artifact means **no directory**.
Extra direct files, nested directories, nested files, symlinks and broken
symlinks are ERROR findings, as are a directory for a record declaring nothing
and a directory belonging to no record. Pre-existing stray-directory and
loose-file errors are unchanged.

**This is not a filesystem lint rule.** It applies to `evidence/artifacts/` and
nowhere else, because that is the one namespace whose entire contents the records
are supposed to enumerate. Doctor reports and never repairs.

Supporting more than one artifact per record would need a schema change and a
decision to match. VS7 does not anticipate one.

## What this does not change

VS6's provenance model is untouched: evaluation snapshots verify exactly as
before, tampered or deleted artifacts still drop out of `trusted`, corpus-content
distinctness and `evaluation_input_digest` are unchanged. The one difference is
that extra content beside a snapshot is now a finding rather than silence, and a
refused write can no longer destroy a snapshot it would previously have
overwritten.

Interruption behaviour is retained, not repaired. An artifact written before its
record still leaves a stray the ledger reports; the counter is persisted first,
so that identifier is never reissued and the stray cannot later be adopted —
and the claim above would refuse it even if it could. No repair, adoption or
cleanup operation is introduced.
