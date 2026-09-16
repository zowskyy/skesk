# DEC-0016 — Packaged resources and the portable content hash

**Status:** Accepted, implemented, tested (Vertical Slice 4).
**Relates to:** DEC-0007 (tamper evidence).

## Two questions

1. How does installed code find data that ships with it?
2. How does a workspace say *which* portable content it installed?

## Resource access: `importlib.resources`, never `__file__`

Bundles live at `skillkernel/assets/skills/<bundle-id>/` and are read through
`importlib.resources.files("skillkernel")`, as `Traversable` objects.

The alternative — deriving a path from `__file__` — works perfectly in a
checkout and is the exact reason a packaged asset ships broken: it encodes an
assumption about layout on disk that a wheel in `site-packages` does not have to
satisfy. `pyproject.toml` declares `package-data = {skillkernel = ["assets/**/*"]}`,
and the acceptance test opens the built wheel and asserts every asset file is
inside it.

**The built wheel is the acceptance surface.** VS2 established that importing
`main()` cannot stand in for running the installed console script; the same
argument applies one level down. A bundle that loads in the checkout and is
absent from `site-packages` is a shipping failure that no in-repository test can
see, so the acceptance module builds a wheel, installs it into a fresh
interpreter that has never seen this repository, and drives the lifecycle from
there.

That module also carries a guard worth naming: every subprocess must run from a
directory outside the checkout. Python puts the working directory on `sys.path`,
so a subprocess started from the repository imports *this* `skillkernel/` no
matter what it installed. The first draft did precisely that, and the fixture's
assertion that `skillkernel.__file__` resolves under `site-packages` is what
caught it.

## The content hash

```
sha256( ALGORITHM_ID || file_count || Σ framed(path, canonical_json(parsed content)) )
```

- Every portable file participates, sorted by POSIX relative path.
- Each file's *parsed* content is rendered through the existing `canonical_json`.
- Each entry is length-prefixed on both path and payload, with fixed-width
  big-endian lengths, so no delimiter can be forged inside either and no two
  different file sets can produce the same byte sequence.
- A domain-separator prefix makes the framing itself part of the input: changing
  the algorithm must change every hash.
- The manifest's own `content_hash` is excluded from its own input — the same
  self-exclusion `definition_hash` already applies to experiment definitions.

### Why parsed content rather than raw bytes

The hash must identify the *portable definition*, not a particular file
rendering. Hashing parsed content makes it immune to indentation, key ordering
and CRLF-versus-LF drift, so the same content hashes identically on every
machine — while preserving every semantic distinction: a changed scalar, a
reordered list, a renamed path, and an added or removed file all change it.

**Precondition, stated because it is load-bearing.** `canonical_json` falls back
to `str()` for a type it does not know, which would let a YAML date collide with
the equivalent quoted string. The bundle schema types every field, so that
collision is unreachable for a bundle that has been validated — and validation
always runs first. This was probed rather than assumed, and the probe is a test.

### What it never covers

The installation directory, the wheel's own bytes, the workspace path, the local
record identifier, timestamps, lifecycle state and evidence. **The container is
never hashed.** Two independent installs of the same bundle into two different
workspaces produce the same hash, and the acceptance test compares the hash
computed in the wheel against the one computed in the checkout.

## Tamper evidence, not immutability

Consistent with DEC-0007. A declared `content_hash` in a manifest is an
integrity *claim*: the catalog computes the hash from the shipped files and
refuses to load a bundle whose claim does not match. Anyone who can rewrite the
files can also rewrite the claim, so this detects drift and accident — an edited
case, a partially applied patch, a corrupted file — not a determined attacker
with write access to the package.

It has already done its job once: an edit to the shipped bundle's procedure was
caught by the mismatch rather than by review.
