# Proposal: Gate-4A per-candidate IC evaluator (阶段8, C1_GPA 先行)

## Why

The quality_profitability_v1 pre-registration is FROZEN (main `4d9fab7`,
2026-07-14; ledger E011 registers the C1 4A dev run). Gate-4A needs a
decision-level evaluator that measures each registered candidate's OOS
factor quality (rank_ic_mean + ic_ir on the dev folds) under EXACTLY the
frozen study design — same universe, same holding cadence, same PIT
discipline the 4B arms will trade — so that a candidate passing 4A is
validated on the track 4B actually runs, not on a lookalike.

Operator-approved decision ledger for this work order (2026-07-14/15):
方案 A (research 侧 IC 评估器); ① C1_GPA 先行, C2/C3 复制跟进;
② 前向收益 = qlib PIT bundle 收盘价按冻结 preset 再平衡日几何;
③ size-decile 来源 = bundle `$total_mv` + 两 pin (停牌 as-of 填充带
陈旧度上限 / CSI300-ever 缺 total_mv 即 fail-loud)。

## Honest state note (for the reviewer/signer)

The implementation is MERGED: PR #354 (main `694f071`) delivered
`scripts/research/gate4a_ic_evaluator.py` + unit tests + a
backward-compatible `include_report_periods` extension to
`FinancialPITDataView.as_of`, hardened through 7 codex rounds
(microstructure mask reuse / ST-on / frozen data-root binding /
PITDataProvider routing / canonical stamp mirroring / cross-endpoint
report-period alignment), r7 verdict CLEAN. The three decision points
below were SIGNED by the operator on 2026-07-15 (DP1 = 20 trading days;
DP2 = JSON single-write; DP3 = span-level hard fail); the DP3 assertion
lands as PR #355. THIS change formalizes the full contract as a spec.

## What changes

- NEW capability `v2-gate4a-ic-evaluation`: the decision-level Gate-4A
  IC evaluation contract (gate precondition, canonical stamp geometry,
  registered-metric scope, forward-return semantics, four-layer
  universe filtering, size-decile construction, frozen data-root
  binding, cross-endpoint period alignment, fail-loud envelope, ledger
  discipline).
- NO factor promotion, NO runtime-path change: research-side only; the
  canonical runtime is untouched (the only shared-module change is the
  opt-in view metadata kwarg, default output byte-identical).

## Decision points (SIGNED by operator, 2026-07-15)

1. **DP1 — total_mv staleness cap = 20 trading days.** Rationale: covers
   routine suspensions (the vast majority resolve well inside a month
   ≈ 21 td) while refusing to size-rank names frozen in long
   restructuring halts on months-old "zombie" market caps; same order
   as one calendar month, well inside the quarterly holding period.
   Names beyond the cap are DROPPED from that stamp's cross-section and
   counted — never silently ranked.
2. **DP2 — artifact format & location.** Proposal: keep
   `output/gate4a/<candidate>_<UTC>/{result.json, report.md,
   gate_accept.txt}` (output/ is gitignored; fold-level series live in
   result.json — 19 primary + tail rows, small enough that JSON is both
   sufficient and reviewable). The signed summary enters git via a
   results-doc PR under `docs/research/` after each run. The original
   work-order sketch said "parquet" — sign JSON as the recorded format,
   or require parquet double-write.
3. **DP3 — "missing total_mv bins" semantics (pin #2 精确化).** Proposal:
   a CSI300-ever member with ZERO `$total_mv` observations across the
   ENTIRE dev span = bundle/registry inconsistency → HARD FAIL (abort
   run); a name transiently missing/stale at a given stamp = drop +
   count (current #354 behavior). SIGNED; the full-span hard-fail
   assertion is delivered by PR #355 (membership-overlap semantics:
   pre-span delistings are exempt — they legitimately have no panel
   data and never enter a cross-section).

## Impact

- specs: + `v2-gate4a-ic-evaluation` (new capability)
- code (already on PR #354): + `scripts/research/gate4a_ic_evaluator.py`,
  + `tests/logic/test_gate4a_ic_evaluator.py`,
  ~ `src/research/financial_pit_view.py` (opt-in kwarg),
  ~ `tests/logic/test_financial_pit_view.py` (battery +1),
  ~ `docs/prereg/quality_profitability_ledger.yaml` (E011)
- DP3 full-span assertion (+ test): PR #355
