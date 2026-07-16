# Recurring review findings → deterministic gates (hardening backlog)

The `@codex` review keeps re-finding the same categories PR after PR — they
are literally the failure patterns `AGENTS.md`'s "Implementation discipline"
section was written from. The local review loop (`local-review-loop.md`)
catches them earlier; this backlog **removes the mechanical ones from the
review's job entirely** by converting them into deterministic pre-commit/CI
checks. That is the repo's own principle: machine-enforce, don't rely on
memory (or on an LLM re-finding it every time).

Each item ships as its own small OpenSpec/PR. Ordered by (pain × automatability).

## 1. [top ROI] Mechanical-move drift detector — DONE (scripts/verify_mechanical_move.py + tests/logic/test_verify_mechanical_move.py)
- **Recurs as:** lost `@dataclass(frozen=True)` / `@classmethod` / `@property`
  decorators, dropped WARNING logs, added `except Exception` catch-alls,
  swapped/dropped keyword-only markers, rewritten docstrings — all of which
  pass an unchanged test suite.
- **Evidence:** `walk_forward.py` split (FIVE hotfix rounds — lost WARNINGs +
  a new `except Exception` in `_run_attribution_for_fold`);
  `tushare/provider_bundle.py` split (one round; 17 tests broke from two lost
  `@dataclass(frozen=True)`).
- **Deterministic check:** `scripts/verify_mechanical_move.py` — (a) auto-runs
  AGENTS.md's prescribed whole-file content-diff (filtered) for the PR's
  renamed/moved files; (b) an AST diff of the pre-move SHA vs the new files
  flagging lost class/func decorators, new broad `except`, and changed
  signatures. CI gate on rename-detected PRs; paste output into the PR body.
- **Effort:** M. **Kills the worst multi-round class.**

## 2. Two-engines schema-parity test
- **Recurs as:** a key added/renamed in Pipeline's `pipeline_report.json` but
  not WalkForward's `walk_forward_report.json` (or `_index.jsonl`), or vice
  versa (`AGENTS.md` "Two engines, one schema").
- **Deterministic check:** a `tests/governance/` test that builds both
  engines' report schemas and asserts identical key sets for the parallel
  artifacts, failing on any asymmetry.
- **Effort:** S.

## 3. Canonical/experimental import-leakage gate (generalize D5)
- **Recurs as:** the #1 governance category in `code-review.md` —
  research/experimental code reachable from canonical runtime.
- **Deterministic check:** an import-linter config (or the D5 per-module test
  generalized): `src/core`, `src/inference`, `feature_dataset_builder`,
  `model_trainer`, pipeline, `daily_recommend` MUST NOT import `research/`,
  factor-mining internals beyond the sanctioned adapter, or the new
  `FinancialPITDataView`. Pre-commit + CI.
- **Effort:** S–M. **Also discharges Gate-2's isolation requirement directly.**

## 4. Silent-fallback scanner — DONE (tests/governance/test_no_silent_fallbacks.py; 20 处现存点已带理由 fallback-ok 注记)
- **Recurs as:** `except …: return {}/None/[]` where the code must `raise`;
  warn-and-proceed on an unknown config key (`AGENTS.md` "No silent fallback").
- **Deterministic check:** an AST lint over `src/core` + `src/data` flagging
  `return {}/None/[]` inside `except` handlers and unknown-key warn-continue
  patterns; allow an annotated `# fallback-ok: <reason>` escape hatch.
- **Effort:** M (some false-positive tuning).

## 5. Import-smoke over ALL changed modules (strengthen the hook)
- **Recurs as:** import-time `NameError` from a removed-but-referenced symbol,
  surfaced only deep inside a `Pipeline.run`.
- **Deterministic check:** extend `.githooks/pre-commit` to run
  `python -c "import X"` for every changed `src/` module derived from
  `git diff --name-only` (AGENTS.md asks for this manually today).
- **Effort:** S.

## Keep as LLM / judgment review (do NOT automate)
Correctness and logic errors, semantic governance drift (as opposed to the
mechanical import-leakage in #3), naming clarity, "does the refactor achieve
its stated goal", and commit-message-matches-diff. These need intent — leave
them to the local review loop + `@codex`.

## Rollout note
Do #2, #3, #5 first (all small, high certainty). #1 is the biggest payoff but
medium effort — worth a dedicated PR. #4 last (needs false-positive tuning).
Each is a normal `feat(governance)` / `chore` PR; none touches canonical
runtime behavior.
