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

## 7b prereg commitments (NOT this change's code; recorded so the campaign
## plan cannot drift from the signed conditions)

- [ ] 2×2 full factorial {N=1, N=5} × {H1, H5}; **four FRESH arms at one
      post-7a commit — stage-6 run reuse CLOSED** (condition 3).
- [ ] **Primary adjudication = paired daily net excess (full sample) +
      gross-alpha collapse veto; sparse-arm ic_1d/ic_5d DEMOTED to
      descriptive statistics with no adjudication standing, in BOTH
      directions** (condition 2 — blocking for 7b).
- [ ] Phase pre-committed (fold_phase, phase=0) + phase-shift sensitivity
      slice; **escalation form pre-commits the winning arm's ST-on
      re-verify under iso_week semantics** (condition 4 — the bridge from
      mechanism evidence to deployable-strategy evidence).
- [ ] **n_drop=5/event pinned as part of the treatment definition; this
      campaign moves cadence ONLY** (condition 5 — the n_drop sweep's
      conclusions stay clean).
- [ ] Honesty envelope: qlib exchange leg non-PIT-wired; weekly holding
      raises delist-during-hold exposure; four arms share it → internal
      comparison valid, absolute levels carry the caveat (condition 6).

## Must-not-touch

- Default (N=1) behavior byte-identical; REGEN-2 anchor green.
- No strategy-layer (Route B) change; no ruler/comparison change.
