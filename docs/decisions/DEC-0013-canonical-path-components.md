# DEC-0013 — Canonical path components are strictly validated

**Status:** Accepted, implemented, tested (Vertical Slice 4, Phase 0A).
**Extends:** DEC-0011.

## Problem

DEC-0011 made a skill's *registered* path agree with its *declared* identity. It
did not constrain what a declared identity may contain. Reproduced at `0f02d22`
through public APIs, in a temporary workspace:

```
slug='../../evil'    ADMITTED
  registered    : 'project/../../evil/skill.yaml'
  written to    : <ws>/evil/skill.yaml
  inside skills/: False
  doctor errors : 0
  ID burned     : True

slug='..'            ADMITTED -> <ws>/skills/skill.yaml
slug='a/b'           ADMITTED -> <ws>/skills/project/a/b/skill.yaml
slug='x/../y'        ADMITTED -> <ws>/skills/project/y/skill.yaml
slug='/absolute'     refused, but only by the post-construction containment check
```

Four of five escaped their scope directory. All were invisible to `doctor`,
because the registered path *equalled its own canonical form* — both contained
the `..` segments, so DEC-0011's comparison found nothing wrong. Each also burned
an identifier.

DEC-0011 guaranteed *registered == canonical*. It did not guarantee *canonical
is well-formed*.

## Decision

A skill slug must match:

```
^[a-z0-9]+(?:-[a-z0-9]+)*$
```

Lowercase ASCII alphanumerics separated by single hyphens, no leading or
trailing hyphen.

## Why this grammar

**It is not a new restriction.** It is exactly what `slugify()` already
produces: NFKD normalize, `encode("ascii","ignore")`, lowercase, collapse each
non-`[a-z0-9]` run to one hyphen, strip the ends. And it is exactly what every
slug in the repository already satisfies — the generated ones and all five
explicit ones (`deterministic-frame-rendering`, `a-different-slug`,
`explicit-slug`, `renamed`, `renamed-slug`).

On Unicode in particular: this bans nothing that works today. `slugify` already
transliterates it away (`'naïve café'` → `'naive-cafe'`), so the grammar
codifies existing behaviour rather than removing a capability.

## Why a grammar rather than a containment check

A string matching this pattern **cannot contain a path separator and cannot be
`.` or `..`**, so it cannot represent more than one component or escape its
scope directory. The property is structural.

Joining arbitrary input and then checking containment is weaker: it constructs
the dangerous path first and relies on catching it afterwards. That is precisely
how `/absolute` was caught while `../../evil` was not — containment stopped the
escape from the repository root but not from the skills tree.

## Enforcement

In `Layout`, inside `skill_path()` and `relative_skill_path()` — the canonical
authority DEC-0011 established. Every caller inherits it, so **no
component-specific validator exists anywhere else**, and none should be added
for bundles or any other input source.

`SkillStore.create()` calls the canonical computation **before
`allocate_id()`**. That ordering is deliberate: `allocate_id()` persists the
registry counter, so validating later would burn an identifier for a slug that
was never going to be legal. A malformed slug now performs *zero* workspace
writes.

Scope needed no new work: it is already a closed enum validated by
`skill_scope_dir`.

## Legacy corruption

`doctor` checks each registry entry's path against
`parse_relative_skill_path()` **using the index alone, before any record is
read**. A malformed path usually makes its own record unreadable, so a
load-first ordering would skip exactly the entries that are worst — a defect
found while writing these tests.

The registry entry is sufficient evidence of corruption. Nothing outside the
managed skill tree is scanned, no traversal path is followed to locate an
escaped artifact, and nothing is repaired.

## Not in scope

Identifier allocation semantics are unchanged. A burned identifier from an
unrelated late failure remains documented, intended behaviour and is not a
partial skill.
