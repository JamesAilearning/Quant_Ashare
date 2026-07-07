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
- [x] reconciliation_residual comparison (equal proxy vs market_cap) on REAL
      fold data — measured on 4 folds of the stage-6 baseline run (full
      300-instrument universe; the earlier "standalone attribution hangs
      >8min" was diagnosed as qlib's multiprocessing pool failing Windows
      handle duplication under a non-interactive shell — `C["kernels"]=1`
      makes the whole comparison ~6s/fold):

      | fold | quarter | eq residual | mc residual | mc/eq |
      |---|---|---|---|---|
      | 01 | 2020Q3 | −0.1111 | −0.1158 | 1.04 |
      | 05 | 2021Q3 | +0.0941 | +0.0608 | 0.65 |
      | 12 | 2023Q2 | +0.0338 | +0.0626 | 1.85 |
      | 20 | 2025Q2 | +0.0486 | +0.0667 | 1.37 |

      **HONEST FINDING: the plan's expectation ("market_cap 下残差应显著小于
      等权代理") is NOT uniformly confirmed** — 1 fold markedly tighter, 1
      flat, 2 wider. The residual bundles more than weight fidelity: the
      Brinson bench leg (Σ w·rᵢ over the ANALYZED universe, price returns)
      is reconciled against the OFFICIAL SH000300TR total-return index, so
      universe coverage, dividends, and the untiered-vs-分级靠档 gap all
      land in it. The feature's justification stands on methodology (the
      cap-weighted bench leg is the correct benchmark model for a
      cap-weighted index; the misnomer trap stays closed) — not on a
      residual improvement this data does not show. All four folds exceed
      the 50bps RECONCILIATION_WARN_THRESHOLD under BOTH methods (it is a
      WARN, not a gate; unchanged by this change).
- [ ] CI green (REGEN-2 leg = anchor unchanged proof); codex clean; merge.
