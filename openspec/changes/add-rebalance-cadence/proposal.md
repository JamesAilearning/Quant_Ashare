# Proposal: rebalance cadence via signal thinning (阶段7 enabler, Route A)

## Why

Two independent quantitative anchors say cadence is the next lever:

1. **④ promotion recon** (docs/promotion/README.md): the promoted canonical
   model has REAL gross alpha (+2.73%/yr, IR +0.32) that daily rebalancing
   eats — cost drag −5.80%/yr at ~9.6% one-side daily fill turnover
   (n_drop=5 → ~5 buys + 5 sells every day). "The alpha exists; daily rebal
   eats it."
2. **阶段6 negative result** (docs/prereg/label_horizon_results.md): the 5d
   label carries signal (ic_5d +15%, CI low up) that a horizon-blind daily
   rebalance cannot monetize — the two arms' cost wedge differed by ~0.01pp.
   Handoff: horizon × cadence must be designed JOINTLY.

This change ships the mechanism only (the 7a enabler); the 2×2 campaign
(7b) runs under its own pre-registration.

## Route decision (operator-signed, Step-0 evidence on file)

**Route A — signal thinning.** Three-level source evidence (qlib pinned at
the canonical commit): `TopkDropoutStrategy.generate_trade_decision` returns
`TradeDecisionWO([], self)` (zero orders = hold) when `get_signal` yields
None; `SignalWCache.get_signal` resamples with `method="last"`; and
`resam_ts_data` slices STRICTLY inside `[start_time, end_time]`
(`selector_datetime = slice(start_time, end_time)`, "return None when the
resampled data is empty") — **no stale-signal backfill**. Emitting
predictions only on rebalance days therefore holds the portfolio on all
other days with ZERO strategy surgery. Route B (strategy-parameter surgery)
was examined and rejected: `TopkDropoutStrategy` has no cadence parameter
(`hold_thresh` is a PER-POSITION minimum-holding constraint, not a portfolio
cadence, and does not reduce buy-side trading); forking the strategy is
heavy surgery on the anchor surface.

**Because the evidence proves "qlib does this today", not "forever": a
CONTRACT TEST is a blocking acceptance item** (operator condition 1). It
runs a REAL qlib backtest over the committed REGEN-2 mini-bundle with a
thinned signal and asserts, on a no-signal day: (a) zero orders, (b)
positions unchanged, (c) the account still accrues market-value returns
that day. A qlib upgrade that flips the empty-window semantics turns this
test red FIRST.

## What (config surface + thinning)

On `WalkForwardConfig` (threaded to `BacktestRunner.run`; single-fold
`PipelineConfig` out of scope for 7a):

- `rebalance_cadence_days: int = 1` — N. Default 1 = today's daily
  rebalance, BIT-IDENTICAL (no filtering at all on the default path).
- `rebalance_phase: int = 0` — the offset (0 ≤ phase < N) of the first
  rebalance day. **N=1 REQUIRES phase=0** (a meaningless combination is
  rejected loudly at config construction — operator small-item 2).
- `rebalance_anchor: "fold_phase" | "iso_week"` (default `"fold_phase"`):
  - `fold_phase`: rebalance days = every Nth TRADING day of the fold's test
    window starting at day `phase`. Per-fold phase reset is deliberate for
    the MECHANISM experiment: each fold starts from cash under the study
    protocol, and 23 folds' phase heterogeneity dilutes weekday effects.
  - `iso_week`: rebalance days = the first trading day of each ISO week
    (production/deployable calendar semantics). **The 7b escalation form
    pre-commits that the winning arm's ST-on re-verify runs under
    `iso_week`** — bridging mechanism evidence to deployable-strategy
    evidence is registered NOW, not invented at promotion time (operator
    condition 4). Shipping both modes in 7a avoids a mid-campaign enabler
    change that would move the prereg plan commit.

Thinning point: `BacktestRunner.run` filters the prediction SIGNAL-STAMP
dates to the rebalance-day set BEFORE `_apply_lag` — **rebalance day = the
signal-stamp day; the fill still happens at T+`signal_to_execution_lag`**
(operator small-item 1; stated in the config docstring verbatim). ST-mask
pairs, the exchange code universe, and the equal-weight baseline's daily
top-k all derive from prediction stamps and thin consistently.

## Known semantic deltas on THINNED arms (default path untouched)

- `SignalAnalyzer` IC is computed on prediction days: sparse arms carry
  ~12 IC days/fold instead of ~60. **7b's prereg demotes sparse-arm
  ic_1d/ic_5d to DESCRIPTIVE statistics with no adjudication standing**
  (operator condition 2, blocking there, recorded here for continuity).
- Fold-22 tail-headroom guard, resume/cache/manifest discipline: the new
  fields join the config → resume fingerprint auto-invalidates (asdict);
  FoldManifest records them additively; the aggregate report embeds them
  via `config` (the #318 template, all acceptance-tested).

## Honesty envelope (registered in 7b's prereg, stated here)

The qlib exchange leg itself remains non-PIT-wired; longer holding raises
delist-during-hold exposure. All four arms share the condition → INTERNAL
comparisons are valid; absolute levels carry the caveat (operator
condition 6).

## Campaign-side commitments this enabler assumes (7b prereg, not 7a code)

- 2×2 full factorial {N=1, N=5} × {H1, H5}; **all four arms run FRESH at
  the same post-7a commit** — reusing the stage-6 run1 is CLOSED (operator
  condition 3; identity is the anchor's job, comparisons never lean on it).
- Phase pre-committed (phase=0) + a phase-shift sensitivity slice.
- **n_drop=5/event is part of the treatment DEFINITION**: this campaign
  moves cadence ONLY; the prior n_drop sweep's conclusions stay
  uncontaminated (operator condition 5).

## Anchor impact

None expected: default N=1 takes the identical code path (no filter
constructed); the REGEN-2 replay passes no cadence arguments. CI judges.

## Out of scope

- `PipelineConfig` (single-fold) cadence; live daily_recommend cadence.
- Any strategy-layer (Route B) change.
- The 7b campaign itself (own prereg, operator-signed numbers).
