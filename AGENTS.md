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

Implementation discipline (mandatory for any code-change task)
These rules are derived from real failure patterns observed across merged PRs in this repository. They are not guidelines; they are blockers. Skip a check, ship a regression — multiple recent PRs have done exactly this and required follow-up commits to repair main.

Before you claim a task is done
Run `pytest tests/logic/ tests/governance/` and read the output. If anything is RED, do not commit. Fix the failure or roll back the change.
Run `python -c "import <module>"` for every source module you touched. This catches import-time `NameError` (a removed-but-still-referenced import) which heavy-path tests will only surface inside a Pipeline.run call.
If you removed an import line, grep the file for the removed symbol. The "moved to top" claim must match the actual file.

Commit messages must match the diff, not the plan
Write the code first, then `git diff --cached`, then write the commit message describing what the diff shows. The message "moved hashlib to module top" is only acceptable when an `import hashlib` line at module top exists in the diff. If the message claims X and the diff lacks X, the message is wrong; fix one of the two before commit.

Changing a contract requires migrating callers and tests
Whenever you change any of: a function signature (parameter name, type, position, default), a dataclass field, the exception type a function raises, or a JSON / dict schema written to disk — `grep -rn '<symbol>' src/ tests/ scripts/` and update every caller. Tests asserting the old behavior must be renamed and rewritten in the same commit. A test named `test_x_rejected_by_validate_input` is broken when the rejection has moved to `__post_init__`; rename to match.

Never invent fields. Always grep the producer.
Before reading `result["foo"]` or `report["section"]["bar"]`, grep the producing code: `grep -rn "'foo'\|\"foo\"" src/`. If the field does not appear at a write site, the field does not exist. Find the actual field. Do not guess from the function name or from a sibling tool's schema.

Match the local style. Read sibling functions before adding a new one.
Before adding a new method, read 2–3 existing methods in the same module. If they `raise X` on bad input, do not silently `return {}` or `return None`. If they log a WARNING with a specific reason string, do the same. Convention divergence within one file is a code smell; either align, or write a comment explaining why divergence is correct.

Refactors must achieve their stated goal
If your commit message says "single source of truth", the diff must delete the duplicate. If the message says "remove magic numbers", the diff must replace literals with named constants. Adding a SECOND source of truth (defence-in-depth) without removing the first is a code smell, not a refactor — the next reader now has two places to update and no clear primary. State the goal honestly or pick a different goal.

Two engines, one schema
This codebase has Pipeline (single-fold) and WalkForwardEngine (rolling). They write parallel artifacts: `pipeline_report.json` / `walk_forward_report.json` / `output/runs/_index.jsonl`. Schema field names MUST be identical across both engines. When you add or rename a key in one, change the other in the same commit. If you cannot, write the symmetric change as a TODO with the exact key list, and stop.

No silent fallback
This codebase rejects implicit fallback. If qlib import fails, raise — do not return `{}`. If positions has the wrong shape, raise — do not silently produce an empty result. If a config key is unknown, hard-fail — do not log a warning and proceed with defaults. Search for the existing "Refusing to silently fall back" comments in the codebase for the canonical pattern.

Test discipline
New behavior requires a test. Test names describe the behavior, not the function — `test_lag_zero_validates_shape` not `test_apply_lag`.
A regression test that requires `RUN_E2E=1` plus a local data bundle is not automated regression coverage; CI cannot run it. Pair every E2E regression test with a synthetic-input unit-level twin that runs without external state.
When fixing a bug, write a test that fails before the fix and passes after. Cite this in the commit body: "before fix: <test> raises X; after fix: passes."

PR scope
One logically coherent change per PR. "P1 batch — N items" is fine ONLY if every item is the same kind (e.g. all defensive-validation tightening). Mixing data-correctness fixes with refactors with new tools makes review impossible.
Follow-up commits within the same PR are a SIGNAL that the original review missed something. When you push a follow-up to a PR you opened, write in the follow-up's body why it slipped past the original review so the next agent reading this can avoid the same gap.
Mechanical-move PRs require pre/post diff verification
A "split this file into a sub-package", "rename this module", or "extract this helper into its own file" task has the explicit goal of zero behavior change. For these PRs a green test suite is necessary but not sufficient — tests cover the properties tests assert on, not all behavior. Lost WARNING logs, dropped keyword-only markers, swapped parameter order, compressed or rewritten docstrings, lost class decorators (e.g. @dataclass(frozen=True)), and quietly-added except Exception catch-alls all pass an unchanged test suite while violating "no behavior change". Before opening the PR, run a whole-file content diff that filters out only trivial lines (imports, blanks, pure docstring rows) so every functional line — including @decorator lines that sit *above* class headers — is compared:
  git show <pre-move-sha>:<old-path> > /tmp/pre.py
  diff <(grep -vE '^(\s*$|\s*#|\s*"""|^import |^from )' /tmp/pre.py | sort) \
       <(cat <new-path-1> <new-path-2> ... | \
          grep -vE '^(\s*$|\s*#|\s*"""|^import |^from )' | sort)
Do NOT use a per-symbol awk window starting at "class X" or "def X" — that pattern excludes the decorator line on the row above and silently misses lost @dataclass / @classmethod / @staticmethod / @property markers. The whole-file content diff catches them because @decorator rows survive the grep filter. Paste the diff output (or the literal text "no diff") into the PR description as proof. Any remaining lines unique to one side are a behavior or contract drift and must either be reverted to the pre-move form or justified in the PR body. Two recent splits required follow-up hotfixes because this verification was skipped or done with the awk-by-symbol form: walk_forward.py (five hotfix rounds — silent drift in _run_attribution_for_fold included lost WARNINGs and a new except Exception catch-all), and tushare/provider_bundle.py (one hotfix round — TushareQlibProviderBundleConfig and TushareQlibProviderValidationProfile both lost their @dataclass(frozen=True) decorators, breaking 17 tests).
Run the review loop locally before you push
The `@codex` PR review should CONFIRM a clean change, not DISCOVER problems. Before you open or update a PR, run the `qlib-code-review` skill against your own diff and iterate to convergence: review (`docs/codex/code-review.md`, P0–P3) → fix P0/P1 → re-review → repeat until the reviewed scope is clean (or only accepted P3 remain). A six-round PR review is the signal this loop was skipped — the review work happened on slow remote round-trips instead of fast local ones. Runbook: `docs/codex/local-review-loop.md`. This loop does NOT replace the mechanical-move whole-file content-diff proof above, nor the deterministic pre-commit/CI gates tracked in `docs/codex/review-findings-to-harden.md` — run each once it lands.
Pre-commit hook
A versioned pre-commit hook ships at `.githooks/pre-commit`. Activate it once per clone with `git config core.hooksPath .githooks`. The hook runs the same import-smoke and targeted-test checks listed above; do not bypass it with `--no-verify` unless explicitly asked by the user.
