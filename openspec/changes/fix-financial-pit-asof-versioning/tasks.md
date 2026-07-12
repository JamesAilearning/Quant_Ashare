# Tasks: fix FinancialPITDataView as-of version selection (Gate-2 correction)

## OpenSpec (propose stage)

- [x] Gate-2 smoke exposed the 1–2yr staleness (7 names, as-of 2024-06-30).
- [x] Root-cause evidence: both-version revenue `uf0`==`uf1` (116 periods, 0
      differ); `update_flag` is a version marker (Gate-1 memo §3).
- [x] Architect cross-review: fix confirmed; land as MODIFIED (not silent bugfix).
- [x] MODIFIED requirement header matches archived
      `openspec/specs/v2-financial-pit-contract/spec.md` (verified vs origin/main).
- [x] Branch the fix off the UPDATED main (worktree off origin/main 53e9036;
      the main checkout was on the stale `chore/codex-review-workflow` branch).
- [x] `openspec validate fix-financial-pit-asof-versioning --strict` green.

## PR — the fix (research-only, still NO factor)

- [x] `select_disclosure_of_record` (financial_pit_contract) + view `_disclosure_frame`:
      per `report_period`, prefer `update_flag=0` else the sole `update_flag=1`;
      as-of serves the LATEST `report_period` with `available_from_trade_date` ≤
      date; a both-version period always resolves to `update_flag=0`.
- [x] Unit tests: uf1-only recent period served (no fallback to older uf0);
      both-version serves uf0 (`test_serves_original_not_revised`); as-of picks
      the latest available period; missing still NA; look-ahead still refused.
- [x] Version-collapse audit (`version_collapse_residual`) + tests: differing-
      `update_flag` fraction over both-version periods; asserts the serve-rule
      resolves differing periods to uf0 (no look-ahead). CI runs the mechanism on
      a synthetic fixture; the full-CSI300-ever residual is produced at ingest.
- [x] Documented the residual (undatable restatement; recent uf1-only silent
      correction) as a known PIT limitation in the view + Gate-1 memo §3 pointer.
- [x] Re-ran the Gate-2 smoke (`D:/qlib_data/financial_pit_smoke`): as-of
      2024-06-30 now serves 2024-Q1 for all 7 names (was 2022–2023; Moutai
      revenue 69.6B→45.8B). Version-collapse residual over the 7-name store
      (NA↔non-NA transitions counted, codex r4): income **0.27%** (2) /
      balancesheet **0.30%** (4) / cashflow **0.00%** (0) of both-version
      field-comparisons differ — a tiny real restatement residual (all NA↔non-NA
      transitions; value-vs-value diffs are 0%). The serve-rule resolves every
      differing period to `update_flag=0`, so it introduces NO look-ahead.
- [ ] Local review loop to convergence before push (`docs/codex/local-review-loop.md`).

## Must-not-touch

- No factor computation; no Gate-3 work.
- No change to ingest (keep both `update_flag` rows), announcement keying,
  missingness, or financial exclusion.
- Canonical runtime / Alpha158 / `daily_recommend` untouched; the isolation
  governance test stays green.
