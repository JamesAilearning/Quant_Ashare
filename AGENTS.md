AGENTS
This file contains lightweight repository-level rules for Codex and other coding agents.
Keep this file concise. Put task-specific workflows in `docs/codex/` or Codex skills so review, implementation, and OpenSpec work do not pollute each other.
When conflicts exist, this file and the current OpenSpec baseline take priority over task-specific guides.
Repository intent
This repository is the clean-slate V2 implementation of the qlib trading system.
V1 is reference-only for lessons learned, migration principles, and governance conclusions.
Do not use V1 as an implementation template by default.
Always-on governance guardrails
Official metrics must come from exactly one canonical qlib-native path.
Experimental behavior must be labeled explicitly and must never be treated as official.
Research artifacts are non-production and non-canonical.
Never silently promote experimental or research behavior into canonical behavior.
Any migration into canonical runtime must be decision-first and semantic-fidelity checked.
Avoid implicit fallback, hidden coupling, and competing official paths.
Layer boundaries
`src/core/`: canonical runtime contracts and approved runtime logic only.
`src/data/`: data access and runtime-adjacent placeholders, but no hidden selection semantics.
`src/contracts/`: schemas, source-of-truth rules, provenance, validation boundaries, status fields.
`web/`: operator-facing and informational views only; do not blur governance meaning.
`research/` and `research/factor_lab/`: research-only, non-production, non-canonical.
`tests/logic/`: runtime and placeholder behavior tests.
`tests/governance/`: contract, boundary, and regression tests.
Task routing
Use the smallest applicable guide:
For code review, PR review, diff inspection, or finding risks: use `docs/codex/code-review.md`.
For implementation, fixes, refactors, or feature work: use `docs/codex/agent-development.md`.
For one OpenSpec loop, proposal/apply/archive work, or OpenSpec governance: use `docs/codex/openspec-loop.md`.
Do not load or apply implementation rules during a review unless the user explicitly asks to fix findings.
Review guidelines
When asked to review code:
Review the diff or changed files first; avoid broad repo exploration unless needed to verify a finding.
Do not edit code, create files, or run destructive commands unless explicitly asked.
Prioritize correctness, governance drift, canonical/experimental leakage, hidden fallback, missing tests, data-contract breakage, and security/reliability risks.
Ignore low-value style suggestions unless they affect maintainability, correctness, or governance clarity.
Use severity labels: `P0` blocker, `P1` high, `P2` medium, `P3` low.
Include file/line references when available.
If no issues are found, say so and mention any validation that was not run.
Change behavior
When asked to implement or fix:
Use OpenSpec for all meaningful changes.
Keep changes minimal, scoped, and archivable.
Prefer foundation-first changes before runtime implementation.
Prefer contract-first changes before UI exposure.
Stop and report conflicts instead of widening scope.
Prefer the smallest compliant implementation.
When unsure, preserve canonical semantics and keep experimental behavior explicit.
Validation expectations
Governance boundaries should be protected by regression tests.
Operator-visible status boundaries should be protected by regression tests where practical.
New contract foundations should include contract-focused tests.
Placeholder runtime components should include tests confirming they remain intentionally unimplemented.
Prefer targeted tests plus repo-wide tests when the change is small enough.
Run `openspec validate` for OpenSpec-affecting changes.
