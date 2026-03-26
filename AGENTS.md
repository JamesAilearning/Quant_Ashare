\# AGENTS



This file defines repository-level rules for Codex and other coding agents.

When conflicts exist, this file and the current OpenSpec baseline take priority.



\## Repository intent



This repository is the clean-slate V2 implementation of the qlib trading system.



V1 is reference-only for lessons learned, migration principles, and governance conclusions.

Do not use V1 as an implementation template by default.



\## Core governance rules



\- Official metrics must come from exactly one canonical qlib-native path.

\- Experimental behavior must be labeled explicitly and must never be treated as official.

\- Research artifacts are non-production and non-canonical.

\- Never silently promote experimental or research behavior into canonical behavior.

\- Any migration into canonical runtime must be decision-first and semantic-fidelity checked.

\- Avoid implicit fallback, hidden coupling, and competing official paths.



\## Layer boundaries



\- `src/core/`: canonical runtime contracts and approved runtime logic only.

\- `src/data/`: data access and runtime-adjacent placeholders, but no hidden selection semantics.

\- `src/contracts/`: schemas, source-of-truth rules, provenance, validation boundaries, status fields.

\- `web/`: operator-facing and informational views only; do not blur governance meaning.

\- `research/` and `research/factor\_lab/`: research-only, non-production, non-canonical.

\- `tests/logic/`: runtime and placeholder behavior tests.

\- `tests/governance/`: contract, boundary, and regression tests.



\## Change rules



\- Use OpenSpec for all meaningful changes.

\- Keep changes minimal, scoped, and archivable.

\- Prefer foundation-first changes before runtime implementation.

\- Prefer contract-first changes before UI exposure.

\- Stop and report conflicts instead of widening scope.



\### Foundation changes

\- May add skeletons, interfaces, placeholders, docs, and tests.

\- Must not implement runtime trading behavior.



\### Contract changes

\- May define schemas, metadata, source-of-truth, validation, status fields, and placeholders.

\- Must not silently implement runtime selection or trading semantics.



\### Runtime changes

\- Must be explicit, narrowly scoped, and spec-approved.

\- Must not widen into unrelated contract or UI work unless explicitly approved.



\### UI changes

\- Must preserve governance wording and boundary clarity.

\- Informational health/status must remain clearly separate from canonical/experimental meaning.



\## OpenSpec loop rule



When asked to run one OpenSpec loop:



1\. Work on exactly one active change.

2\. Use `/opsx:propose` only when no approved active change is ready for implementation.

3\. Use `/opsx:apply` only when the current change is proposal-complete and validated.

4\. After apply, always do a review checkpoint:

&#x20;  - changed files

&#x20;  - implemented scope

&#x20;  - intentionally unimplemented items

&#x20;  - tests run

&#x20;  - `openspec validate`

&#x20;  - scope drift / contract drift / governance drift check

5\. Use `/opsx:archive` only when the change is complete, validated, tested, and still within approved scope.

6\. Do not start the next change automatically unless explicitly asked.

7\. Never work on more than one change in a single loop.



\## Data contract rules



\- Benchmark, taxonomy, universe, and similar artifacts require explicit source-of-truth and provenance.

\- Validation health is informational unless a policy explicitly defines hard-fail behavior.

\- Contract validation must remain separate from runtime selection semantics.



\## Testing expectations



\- Governance boundaries should be protected by regression tests.

\- Operator-visible status boundaries should be protected by regression tests where practical.

\- New contract foundations should include contract-focused tests.

\- Placeholder runtime components should include tests confirming they remain intentionally unimplemented.

\- Prefer targeted tests plus repo-wide tests when the change is small enough.



\## Archive checklist



Before recommending archive, confirm:



\- the scoped change is complete

\- tests passed

\- `openspec validate` passed

\- docs/tasks were updated

\- no runtime semantics changed beyond approved scope

\- no experimental or research behavior leaked into canonical behavior

\- no implicit fallback or hidden coupling was introduced



\## Default conflict behavior



\- If a request conflicts with AGENTS.md, the current OpenSpec baseline, or approved scope, stop and explain the conflict.

\- Prefer the smallest compliant implementation.

\- When unsure, preserve canonical semantics and keep experimental behavior explicit.

