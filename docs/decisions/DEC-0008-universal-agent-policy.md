# DEC-0008 — Universal agent policy ships as a bundled core skill

**Status:** Accepted (design). Policy text written; delivery mechanism **not
implemented**.
**Date:** after Milestone 0.

## Question

Operating rules for agents — chiefly the two-method escalation rule — are
project-agnostic. Every project that uses SkillKernel wants the same escalation
behaviour. How should a new repository receive it?

## Options considered

1. **Copy the text into each project's `AGENTS.md` by hand.** Works, but it is
   exactly the "giant manually maintained instruction document" this system
   exists to remove. The copies drift, and no copy can be traced to a source.
2. **Reference a remote canonical document.** Makes kernel behaviour depend on a
   network service, which B5 forbids.
3. **Ship it as a bundled core skill installed by `skillkernel init`.**

## Decision

Option 3. The universal agent policy becomes one of the small set of bundled
core skills that `skillkernel init` installs into a new repository.

This is the right shape for it because the policy *is* a skill under this
project's own definition: a procedure, with explicit activation boundaries
("when you hit a blocker you cannot confidently resolve"; *not* "for routine
work proceeding normally"), success conditions, failure modes and verification.
It is not background prose. Treating it as a skill means it gets an identifier,
provenance, a maturity, and the same evidence discipline as everything else.

## Structure

The split is load-bearing and is now reflected in this repository:

```
Universal agent rules            docs/policies/universal-agent-rules.md
├── two-method escalation rule
├── no false verification
├── no placeholder completion
├── preserve user work
├── test before completion
├── report blockers honestly
└── stop on destructive ambiguity

Project-specific rules           AGENTS.md, Part B
├── allowed technologies
├── architectural constraints
├── budget restrictions
├── performance requirements
├── required tests
└── project-specific stop conditions
```

The universal half must stay free of any SkillKernel assumption, so it can be
lifted into an unrelated repository unchanged.

## Maturity on arrival

`candidate`, per `DEC-0004`. A bundled skill carries no evidence from the
repository it lands in, and marking it `validated` would manufacture exactly
the kind of unearned confidence this system is built to prevent.

This is worth stating plainly because the policy is one we are confident in:
confidence is not evidence, and the kernel must not make an exception for a
rule it happens to like.

## What is NOT implemented

Stated so this record is not mistaken for a delivered capability:

- `skillkernel init` does not exist.
- The skill storage layer (`SkillStore`) does not exist.
- No bundled skill assets exist.
- Therefore **the policy is not yet installable**. It currently reaches this
  repository the manual way — as `docs/policies/universal-agent-rules.md`,
  summarized in `AGENTS.md` — which is option 1, the option this decision
  rejects for the long run.

That gap is deliberate. Building the installer now would mean building
Vertical Slices 1 and 3 ahead of the acceptance test that should drive them.
The policy text existing first is useful on its own, and it becomes the first
real content for the installer when it is written.

## Required shape when implemented

The skill must carry, in `skill.yaml`:

- `classification.scope: core`, `classification.maturity: candidate`
- `provenance.created_by: bundled`
- non-empty `applies_when` **and** `do_not_apply_when` — the escalation rule
  must not fire on routine work, and over-activation is the dangerous direction
- `activation_rules` distinguishing "blocked, ambiguous, or a second method has
  failed" from ordinary progress
- `procedure` — the two-method budget and the escalation report format
- `failure_modes` — including the realistic one: disguising three variants of
  one approach as three methods
- `verification` — an escalation report containing all eight required sections

Positive and negative example fixtures must cover both directions, since
`DEC-0003` requires false activations to be reported separately from missed
ones.

## Consequence for the kernel

This is the first concrete instance of the kernel's own premise: a rule that
would otherwise live as prompt folklore becomes a versioned, identified,
evidence-bearing record. If the mechanism cannot carry this policy cleanly,
that is evidence about the mechanism, not about the policy.
