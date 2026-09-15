# Decision records

Design decisions, their rationale, and the evidence available when they were
taken. Historical decisions stay inspectable: a superseded decision is marked
superseded and kept, never deleted.

These are written as Markdown because the machine-readable decision record type
(`DEC-nnnn`, per the design directive) is not implemented yet. When it is, these
files become the human projection of those records.

| ID | Status | Decision |
| --- | --- | --- |
| [DEC-0001](DEC-0001-schema-engine.md) | Accepted (provisional) | Hand-written schema engine rather than a third-party library |
| [DEC-0002](DEC-0002-structure-deviations.md) | Accepted | Package structure deviations from the design directive |
| [DEC-0003](DEC-0003-activation-scoring.md) | Accepted | Skill evaluation scores activation boundaries deterministically |
| [DEC-0004](DEC-0004-bundled-core-skills.md) | Accepted | Bundled core skills enter at `candidate`, not `validated` |
| [DEC-0005](DEC-0005-generated-skill-docs.md) | Accepted | `skill.yaml` is authoritative; `SKILL.md` is generated |
| [DEC-0006](DEC-0006-behavior-fingerprint.md) | Accepted | Behaviour fingerprint covers behavioural fields only |
| [DEC-0007](DEC-0007-tamper-evidence.md) | Accepted | The evidence ledger provides tamper evidence, not immutability |
| [DEC-0008](DEC-0008-universal-agent-policy.md) | Accepted (design) | Universal agent policy ships as a bundled core skill installed by `skillkernel init` |
| [DEC-0009](DEC-0009-slice1-gate-scope.md) | Accepted | Promotion gates check fields and evidence, not generated documents |
| [DEC-0010](DEC-0010-cli-exit-codes.md) | Accepted | CLI exit-code doctrine; exit 70 for unexpected internal failure |
| [DEC-0011](DEC-0011-skill-location-identity.md) | Accepted | A skill's location-bearing identity (`scope`, `slug`) is immutable through ordinary persistence |
| [DEC-0012](DEC-0012-workspace-independent-doctor-output.md) | Accepted | Machine-readable doctor output is workspace-independent |
| [DEC-0013](DEC-0013-canonical-path-components.md) | Accepted | Canonical path components are strictly validated |
| [DEC-0014](DEC-0014-portable-skill-definition.md) | Accepted | A bundle is a portable definition; source provenance is an `x_source` extension field |
| [DEC-0015](DEC-0015-bundle-installation-semantics.md) | Accepted (amends DEC-0004, DEC-0008) | Bundle installation semantics: local identity, `observed` on arrival, refusal over reconciliation |
| [DEC-0016](DEC-0016-packaged-resource-integrity.md) | Accepted | Packaged resources via `importlib.resources`; the portable content hash |
