"""The promotion engine.

One entry point, three checks in a fixed order:

    1. is the transition shaped correctly?   (the state machine)
    2. is it earned?                         (the gate)
    3. record it                             (history, then the skill)

Order matters. The state machine rejects nonsense like ``candidate -> trusted``
before the gate wastes effort proving evidence for a move that could never be
legal. And because the gate runs before anything is written, a refused promotion
leaves the skill exactly as it was: there is no partial application to unwind.

The engine is the only thing that changes a skill's maturity. ``SkillStore`` can
edit content, but not lifecycle state — that separation is what keeps every
maturity traceable to a recorded, gated transition.
"""

from __future__ import annotations

from collections.abc import Sequence

from skillkernel.core.clock import now_iso
from skillkernel.core.config import KernelConfig, load_config
from skillkernel.core.errors import GateError
from skillkernel.core.paths import Layout
from skillkernel.promotion.gates import GateReport, check_gate
from skillkernel.skills.maturity import check_transition
from skillkernel.skills.model import SkillRecord
from skillkernel.skills.store import SkillStore

__all__ = ["PromotionEngine"]


class PromotionEngine:
    def __init__(self, layout: Layout, *, config: KernelConfig | None = None) -> None:
        self.layout = layout
        self.skills = SkillStore(layout)
        self._config = config

    @property
    def config(self) -> KernelConfig:
        if self._config is None:
            self._config = load_config(self.layout)
        return self._config

    def dry_run(self, skill_id: str, target: str) -> GateReport:
        """Report whether a promotion would be allowed, changing nothing."""
        skill = self.skills.require(skill_id)
        check_transition(skill.maturity, target)
        return check_gate(self.layout, skill, target, config=self.config)

    def promote(
        self,
        skill_id: str,
        target: str,
        *,
        reason: str,
        actor: str,
        evidence: Sequence[str] = (),
        now: str | None = None,
    ) -> SkillRecord:
        """Promote a skill, or raise explaining exactly why it cannot be promoted."""
        if not reason.strip():
            raise GateError("a promotion must state a reason")

        skill = self.skills.require(skill_id)
        previous = skill.maturity

        # 1. Shape. Raises TransitionError for an illegal move.
        check_transition(previous, target)

        # 2. Earned. Nothing has been written at this point, so a refusal here
        #    leaves the skill untouched.
        report = check_gate(self.layout, skill, target, config=self.config)
        if not report.passed:
            raise GateError(
                f"{skill_id} cannot be promoted from {previous!r} to {target!r} because it",
                report.reasons,
            )

        # 3. Record. History first, then the skill record.
        cited = sorted({*evidence, *report.evidence_used})
        return self.skills.record_transition(
            skill_id,
            previous_state=previous,
            new_state=target,
            reason=reason,
            actor=actor,
            evidence=cited,
            gate_result=report.to_document(),
            now=now or now_iso(),
        )

    def deprecate(
        self,
        skill_id: str,
        *,
        reason: str,
        actor: str,
        replaced_by: str | None = None,
        now: str | None = None,
    ) -> SkillRecord:
        """Deprecate a skill, recording the rationale on the record itself."""
        skill = self.skills.require(skill_id)
        timestamp = now or now_iso()
        check_transition(skill.maturity, "deprecated")

        document = dict(skill.raw)
        document["deprecation"] = {
            "reason": reason,
            "replaced_by": replaced_by,
            "at": timestamp,
        }
        document["updated_at"] = timestamp
        self.skills.save(SkillRecord.from_document(document, source=skill_id))

        return self.promote(skill_id, "deprecated", reason=reason, actor=actor, now=timestamp)
