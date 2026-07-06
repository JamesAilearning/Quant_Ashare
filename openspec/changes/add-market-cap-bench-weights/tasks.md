# Tasks: market_cap benchmark weights (audit P6)

- [x] Preflight re-run (2026-07-06): daily_basic merged (#182–#188), raw
      2018–2026, `circ_mv.day.bin` present in the PIT bundle;
      `instruments/csi300.txt` carries (start, end) membership intervals;
      attribution confirmed outside the REGEN-2 pin (#320 precedent).
- [x] `_market_cap_weights`: PIT `$circ_mv` as-of `<= T0` (30-day lookback),
      normalized; fail-loud on no-provider / missing / non-positive.
- [x] `_validate` gains the provider-aware market_cap gate (explicit-weights
      path and all other methods unchanged); `_resolve_benchmark_weights`
      and the `analyze` call thread `pit_provider`.
- [x] Misnomer discipline preserved: `_effective_bench_weight_method`
      unchanged; sentinel comment + config docstring rewritten as-built
      (honest approximation note included).
- [x] Tests: weight correctness + normalization; as-of freshest-`<=T0` wins
      (lookahead trap); missing / all-NaN / non-positive / empty-panel /
      no-provider fail-loud; provider-accepted validation; label test.
      Existing "requires benchmark_weights" market_cap test upgraded to the
      new contract.
- [x] Baseline spec requirement rewritten from "reserved, SHALL fail" to
      the implemented contract (delta in this change; baseline synced in
      the same PR per the #322/#326 archive lesson).
- [ ] reconciliation_residual comparison (equal proxy vs market_cap) on real
      fold data — ATTEMPTED locally (fold_05 of the stage-6 baseline run,
      full universe AND a held-universe subset): the STANDALONE attribution
      path on a cold process exceeds 8 minutes wall-clock per call on this
      box, so the honest comparison is DEFERRED to the first engine-context
      exercise of market_cap (in-process warm caches, seconds per fold) —
      record the numbers then. Never fabricated from synthetic data.
- [ ] CI green (REGEN-2 leg = anchor unchanged proof); codex clean; merge.
