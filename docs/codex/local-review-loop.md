# Local review loop (run before you push)

The `@codex` PR review should CONFIRM a clean change, not DISCOVER problems.
When a PR needs six or seven review rounds, the review work is happening on
slow remote round-trips instead of fast local ones — and each round can
introduce new findings from the previous round's fixes. Move the loop local.

## The loop

1. Implement (per `docs/codex/agent-development.md`).
2. **Local review** — run the `qlib-code-review` skill against the working
   diff. It reads `AGENTS.md` + `docs/codex/code-review.md` and emits P0–P3
   findings for a diff / uncommitted changes (its declared scope). Suggested
   prompt (from `code-review.md`):
   > Review the current diff using docs/codex/code-review.md. Do not modify files.
3. **Fix** P0/P1; triage P2/P3 (fix, or explicitly accept with a reason).
4. **Re-review the new diff.** Fixes can introduce new findings — that is
   exactly what the remote rounds were catching. Iterate until the reviewed
   scope is clean or only accepted P3 remain.
5. Run the pre-commit checks (`pytest tests/logic tests/governance`,
   import-smoke) and `openspec validate` for OpenSpec-affecting changes.
6. **Then** push. `@codex` now confirms instead of discovering.

## Convergence criterion

Stop when a fresh local review of the FINAL diff returns no P0/P1 and no
un-accepted P2. Record accepted P2/P3 in the PR body so the remote review
does not re-litigate them.

## This loop does NOT replace

- The **mechanical-move whole-file content-diff proof** (`AGENTS.md`) for
  split/rename/extract PRs. The content-diff deterministically catches lost
  `@dataclass(frozen=True)` / WARNING logs / added `except Exception` that an
  LLM review and a green test suite both miss. Run both.
- The **deterministic pre-commit/CI gates** in
  `docs/codex/review-findings-to-harden.md` — recurring mechanical findings
  are being converted from review-caught to CI-caught so the review stops
  re-finding them.

## Why it is worth it

The recurring findings are mostly the known failure patterns already listed
in `AGENTS.md`'s "Implementation discipline" section. Catching them locally,
pre-push, converts N slow remote rounds into one or two fast local ones —
and `@codex` on the PR becomes a confirmation, not a discovery session.
