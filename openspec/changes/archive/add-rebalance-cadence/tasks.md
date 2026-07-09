# Tasks: rebalance cadence via signal thinning (阶段7 enabler)

## OpenSpec (propose stage)

- [x] Step-0 recon (2026-07-07, read-only, operator-reviewed): three-level
      qlib source evidence for the no-signal-day hold (strategy None-branch /
      SignalWCache method="last" / resam_ts_data strict in-window slice, no
      backfill); Route B rejected on evidence (no cadence param;
      hold_thresh = per-position min-hold, not portfolio cadence).
- [x] Operator sign-off: Route A + fold-phase calendar semantics, with 7
      conditions (all folded into the items below / the 7b prereg) + 2
      inline items (doc line; N=1→phase=0 validation).
- [x] `openspec validate add-rebalance-cadence --strict` green.

## PR — 7a enabler (single PR: spec + implementation; unblocked, the
## add-daily-decision-page bookkeeping closed in #334)

- [x] Config surface: `rebalance_cadence_days: int = 1`,
      `rebalance_phase: int = 0`, `rebalance_anchor: "fold_phase"|"iso_week"`
      on `WalkForwardConfig`; validation fail-loud on: non-positive/
      non-int N, phase outside [0, N), unknown anchor, and **N=1 with
      phase≠0 (meaningless combination, operator small-item 2)**; docstring
      pins **"rebalance day = the signal-stamp day; the fill still happens
      at T+signal_to_execution_lag" (operator small-item 1)**.
- [x] Thinning in `BacktestRunner.run` (params threaded from the engine):
      filter signal-stamp dates to the rebalance-day set BEFORE `_apply_lag`;
      `fold_phase` = every Nth trading day of the evaluation window from day
      `phase`; `iso_week` = first trading day of each ISO week. N=1
      constructs NO filter (bit-identical default path).
- [x] **CONTRACT TEST (operator condition 1 — BLOCKING acceptance):** a real
      qlib backtest over the committed REGEN-2 mini-bundle with a thinned
      signal asserts, on a no-signal day: (a) ZERO orders, (b) positions
      unchanged day-over-day, (c) the account still accrues market-value
      returns that day. A qlib upgrade flipping the empty-window semantics
      turns this red before anything else does.
- [x] Identity tests: N=1 produces byte-identical prediction input to the
      strategy (no filter object in the path); default-config fold metrics
      unchanged; REGEN-2 anchor leg green in CI (the real judge).
- [x] Cadence-day derivation tests: fold_phase (N=5, phase 0/2; window not
      divisible by N; fold shorter than N) and iso_week (holiday-shifted
      week starts; year boundary) — plus phase-validation rejections.
- [x] Discipline (#318 template): resume fingerprint picks the new fields up
      via asdict (test: N=1 vs N=5 fingerprints differ); FoldManifest
      records them additively with named-cause re-run messaging; aggregate
      report carries them via the embedded config (test).
- [x] Consistency deltas: the schedule is derived from the evaluation
      window's TRADING CALENDAR (codex P2 #336), not the prediction index —
      a scheduled day absent from predictions HOLDS (test pins it) rather
      than sliding the cadence. ST-mask pairs and the exchange code universe
      read `shifted_predictions` (thinned by construction; the contract
      probe proves the strategy + exchange path via positions changing only
      on rebalance days). The equal-weight baseline is OMITTED for a
      non-daily cadence (codex P2 #336): its one-day-hold shape can't
      represent a held-across-days arm, so it is dropped with a WARN rather
      than published misleadingly — pinned by the contract probe.
- [x] Whitelist/governance sweep untouched (no new qlib call sites) —
      verified by the existing counting tests.

## 7b prereg commitments — DELIVERED (campaign concluded 2026-07-09,
## pre-reg docs/prereg/cadence_horizon.yaml; DEAD-END verdict)

- [x] 2×2 full factorial {N=1, N=5} × {H1, H5}; four FRESH arms at one
      commit `74f2c50` (reuse CLOSED; daily_h1 reproduced 阶段6 h1_st_off
      byte-for-byte, condition 3).
- [x] **Primary adjudication = paired daily net excess + gross-collapse
      diagnostics; sparse-arm ic_1d/ic_5d descriptive-only both directions**
      (condition 2). Verdict: INDISTINGUISHABLE, no arm sig-positive → DEAD-END.
- [x] Weekly = iso_week (operator-signed; the campaign uses the deployable
      calendar directly, so the winner's ST-on re-verify would be the SAME
      schedule — condition 4 bridge preserved without a fold_phase→iso_week
      shift). fold-0 / 2020H2 slices ran (state stable); no phase slice
      (iso_week is calendar-pinned).
- [x] **n_drop=5/event pinned across all arms; cadence-ONLY** (condition 5).
- [x] Honesty envelope recorded in the results doc; predicted outcome held
      (mechanism works — net +1.33pp — but the +2.73% gross base is too thin
      to clear the noise floor) (condition 6).

## Must-not-touch

- Default (N=1) behavior byte-identical; REGEN-2 anchor green.
- No strategy-layer (Route B) change; no ruler/comparison change.
