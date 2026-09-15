# DEC-0003 — Skill evaluation scores activation boundaries

**Status:** Accepted. Mechanism partly implemented (`activation_rules` and
`SkillRecord.applies_to`); the evaluator itself is not built yet.

## Question

What does it mean, deterministically and without a model in the loop, to
evaluate a skill?

## Decision

Score **activation boundaries** against labelled positive and negative example
cases.

A skill carries machine-checkable `activation_rules` (`require_any`,
`require_all`, `exclude_any`) alongside its prose `applies_when` /
`do_not_apply_when`. Example cases carry signals and an expected outcome. The
evaluator scores predicted activation against expected activation.

## Rationale

A skill system must answer two questions, not one:

- Did the procedure work?
- Did the system know **when to use it**, and when **not** to?

The second is what makes a skill library safe to grow. It is also fully
deterministic, requires no model, and makes the directive's claim that
"activation boundaries are first-class behavior" mechanically checkable rather
than aspirational.

## Reporting requirement

The evaluator must report, separately:

- activation true positives
- activation true negatives
- false activations (fired when it should not have)
- missed activations (did not fire when it should have)
- execution success

**These must not be collapsed into a single score.** A skill that fires on
everything scores well on recall and is actively harmful; an aggregate would
hide exactly that. False activation is the dangerous direction and is tracked
on its own.

## Already enforced

Activation fails closed: a skill with no rules never applies, and exclusions
beat inclusions. Both are tested.
