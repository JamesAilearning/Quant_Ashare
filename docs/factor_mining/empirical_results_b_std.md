# Empirical results: GP factor mining on B-std (csi300, 2018-2025)

> **⚠️ SUPERSEDED (C2-b, 2026-06-05).** The "GP loses" conclusion below was
> produced on a CONTAMINATED comparison: the walk-forward predated the #212
> embargo gap (train→valid / valid→test label look-ahead) AND scored GP
> factors over (part of) the same period they were selected on (IS/OOS
> selection bias). Those numbers are unreliable as stated. The clean, fair
> verdict — leak-free embargo-gapped folds, frozen IS-only factor selection,
> same-window Alpha158 baseline — is in `docs/phase_c2b_dryrun_result.md`
> (decision: `decisions.md` D6). It independently reaches the same direction
> (GP shelved), but rigorously. Keep this document for the root-cause
> analysis only.

This document records the first end-to-end empirical evaluation of the
factor-mining subsystem against the design doc's success criterion
(§10: "Adding mined factors improves OOS Sharpe ≥ 10% vs Alpha158-only
baseline"). It was the first such evaluation; its verdict is **superseded**
(see the note above), so this file is no longer the canonical reference. The
canonical answer to "does GP factor mining work as configured today?" now
lives in `docs/phase_c2b_dryrun_result.md` (decision: `decisions.md` D6); this
file is kept for its root-cause analysis only.

**TL;DR: No, not as currently configured.** The infrastructure is
complete and bug-free, but on a real 8-year csi300 walk-forward the
GP-mined pools systematically underperform the Alpha158 baseline.
The §10 IR threshold was not met in any of the three experimental
configurations tried (default fitness, soft fitness, soft-top-20).
This document captures the numbers, the root-cause analysis from the
GP pool itself, and the concrete follow-up directions that would
plausibly move the needle. Code-wise the system is shippable; the
GP-on-OHLCV recipe just doesn't beat 158-feature hand-engineering on
this universe / window.

## Scope and assumptions

- **Data**: PIT-corrected qlib bundle covering 2018-01-02 → 2025-12-31
  (1942 trading days, 5847 instruments, csi300 universe). Built per
  `inventory.md` §F.3 from Tushare `daily` + `adj_factor` + index
  membership endpoints.
- **Walk-forward**: 23-fold rolling window with the canonical
  `train_months=24 / valid_months=3 / test_months=3 / step_months=3`
  sizing (see `config_walk.yaml`). First fold's test window is
  2020-04-01 → 2020-06-30; last is 2025-10-01 → 2025-12-31.
- **GP training data**: 2018-01-01 → 2023-12-31 (six years). The
  2024-2025 portion of the bundle is kept out of GP exposure so the
  walk-forward folds whose test window lands there are fully OOS for
  factor expression selection.
- **Baseline**: Alpha158 handler (158 hand-engineered features built
  into qlib).
- **Candidate**: MinedFactor handler with a top-K factor pool produced
  by `python -m src.factor_mining.miner ...`.

The bake-off CLI is `scripts/compare_factor_handlers.py`; the
threshold check is `candidate.mean_information_ratio >= 1.10 *
baseline.mean_information_ratio`.

## Experiments run

Three GP configurations, all with `population_size=200` and
`n_generations=20` (4000 evaluations each):

| Experiment | Fitness weights | `pool_top_k` | Why |
|---|---|---|---|
| **default** | §5.1 design defaults: `w_corr=0.8, w_turnover=0.2, cost_rate=0.003, w_complexity=0.01` | 50 | first principled run on B-std |
| **soft** | softened penalties: `w_corr=0.1, w_turnover=0.05, cost_rate=0.001, w_complexity=0.005` (IC/IR/rank-IC weights unchanged) | 50 | hypothesis: §5.1 novelty pressure dominates the fitness landscape, kills signal-strong candidates |
| **soft-top-20** | same fitness as `soft` | 20 (in-process truncation of `soft` pool) | hypothesis: 50 features still let LightGBM overfit on ~250k-row train folds; tighter pool = better generalisation |

Configs are pinned under `config/factor_mining/pit_full.yaml`,
`config/factor_mining/pit_full_soft.yaml`. The soft-top-20 pool was
produced by `_truncate_pool_to_top_k(soft_pool, 20)` without re-mining.

## Headline numbers

| Metric | Alpha158 baseline | default | soft | **soft-top-20** | Design-doc target |
|---|---:|---:|---:|---:|---|
| mean_information_ratio | **+0.466** | -0.304 | -0.126 | **-0.094** | ≥ 1.10× baseline |
| mean_ic_1d | **+0.0247** | -0.0020 | +0.0033 | **+0.0060** | (informational) |
| mean_annualized_return | **+4.90%** | -2.66% | +0.52% | **+0.11%** | (informational) |
| worst_drawdown | -12.14% | **-7.14%** | **-8.46%** | **-7.04%** | (informational) |
| `valid_folds_ic_1d` | 22 / 23 | 18 / 23 | 18 / 23 | 16 / 23 | (informational) |
| `design_doc_ir_threshold_met` | — | **FALSE** | **FALSE** | **FALSE** | TRUE |

All three MinedFactor candidates fall short on the §10 IR criterion.
The trajectory `default → soft → soft-top-20` shows the
parameter-tuning direction is correct (every step improves OOS IR
toward Alpha158's number) but the rate of improvement is hitting a
ceiling and the gap to baseline is still wide. The only metric where
MinedFactor wins is `worst_drawdown` — but the mechanism is
unflattering: factors with near-zero IC produce more diversified
(closer to random) selection, which by Jensen-inequality flavour
reduces single-stock concentration risk.

## Root-cause analysis: why GP underperformed

Three findings, surfaced by inspecting the GP `factor_pool.parquet`
and `gp_history.json` for each run plus a manual review of the top
expressions.

### Finding 1: §5.1 novelty weight (`w_corr=0.8`) crowded out signal

The composite fitness is

```
fitness = w_ic·|IC| + w_ir·IR + w_rankic·|rank_IC|
        − w_turnover·(turnover_daily · 252 · cost_rate)
        − w_corr·novelty_penalty
        − w_complexity·expr_size
```

With default `w_corr=0.8`, a candidate with `|IC|=0.04` and
`novelty=0.6` (i.e. 60% correlated with existing pool members) scored
≈ `0.04 - 0.48 ≈ -0.44`. A candidate with `|IC|=0.005` and
`novelty=0.10` scored ≈ `0.005 - 0.08 ≈ -0.075` — i.e. a 5×-weaker
factor *beat* the stronger one purely because of orthogonality. The
GP correctly optimised the configured objective; the configured
objective just rewarded noise-with-novelty over signal.

The default pool reflected this: median `|IC| = 0.005`, max `|IC| =
0.013`, every entry's fitness negative. The soft pool relaxed
`w_corr=0.1` and median `|IC|` jumped to 0.017 (3.4×); fitness range
flipped to `[+0.007, +0.040]`. But the OOS gain was much smaller
than the IS gain — the IS-OOS gap is the next finding.

### Finding 2: IS-OOS IC gap of 5-6× = classic factor overfit

| | Default | Soft | Soft-top-20 |
|---|---:|---:|---:|
| Median IS `\|IC\|` (training period 2018-2023) | 0.005 | 0.017 | 0.018 |
| Mean OOS `IC` (walk-forward folds) | -0.002 | +0.003 | +0.006 |
| IS / OOS ratio | n/a (sign-flip) | ≈ 6× | ≈ 3× |

A 5-6× degradation between in-sample and out-of-sample IC is a
textbook overfit signature. With pop=200 × gen=20 = 4000 evaluations
over a 6-year × ~300-ticker training set, the GP has enough flexibility
to fit period-specific quirks but the expressions don't generalise to
the rolling test windows.

`pool_top_k=20` cuts the IS-OOS gap to ≈ 3× and lifts mean OOS IC to
+0.006, but the improvement is marginal — the underlying factor
representation is the limit, not the post-hoc truncation.

### Finding 3: Top expressions reveal pseudo-signals and low diversity

Manual review of the `soft` pool's top 10 by fitness:

```
 #  fitness  IS-IC   expression
----------------------------------------------------------------------------------
 1  +0.040  +0.013  cs_winsorize(ts_skew(neg(log_safe($open)), 60))
 2  +0.037  +0.013  cs_zscore(ts_skew(neg(log_safe($open)), 60))
 3  +0.036  +0.021  cs_demean(ts_corr(log_safe($high), $high, 20))    ← pseudo-signal
 4  +0.036  +0.011  cs_zscore(ts_mean(ts_argmin($volume, 40), 5))
 5  +0.034  +0.018  cs_demean(abs(ts_rank(neg(log_safe($close)), 5)))
 6  +0.033  +0.010  cs_rank(sqrt_safe(sqrt_safe(ts_argmin($volume, 60))))
 7  +0.033  +0.012  cs_zscore(ts_skew(neg(log_safe($open)), 10))
 8  +0.033  +0.022  cs_zscore(ts_corr(log_safe($close), $close, 60))  ← pseudo-signal
 9  +0.032  +0.022  cs_zscore(ts_corr(log_safe($close), $close, 20))  ← pseudo-signal
10  +0.032  +0.010  cs_demean(sqrt_safe(sqrt_safe(ts_argmin($volume, 60))))
```

Three observations:

1. **Three of the top four IS-IC entries are mathematical pseudo-signals.**
   `ts_corr(log_safe(X), X, N)` correlates a price series with a
   monotonic function of itself — over a rolling window the correlation
   is mechanically near 1 with tiny variation driven by log compression
   at low prices, not predictive content. These factors look "best"
   in IS but have no economic mechanism, so OOS performance is whatever
   the log-compression artefact happens to do.

2. **Three semantic templates repeat across all 10 entries:**
   `ts_skew(neg(log_safe(price)), N)`, `ts_corr(log_safe(X), X, N)`,
   `ts_argmin($volume, N)`. The remaining `cs_*` decorations are
   normalisations. 50 GP factors, ~5 semantic types — diversity is
   poor.

3. **`coverage` is uniformly 0.51-0.56** — every kept factor is
   computing on barely-more-than-half the date-ticker cells (after
   `coverage_min=0.5` floor relaxation; the design-doc default is 0.8).
   High-coverage factors didn't make the cut.

For context, Alpha158's 158 features cover ~50 distinct semantic
types (rolling returns, price ratios, range/band features, momentum,
EMA, volatility, RSI variants, and fundamental ratios via
daily_basic). The MinedFactor pool covers ~5 semantic types in
the entire top-50. **This is an expressivity-gap problem, not a
fitness-tuning problem.**

## What worked

- **Infrastructure**: PIT bundle build, evaluator, fitness pipeline,
  GP engine, factor pool persistence, MinedFactor handler, walk-forward
  integration, compare CLI — all functional. End-to-end runs complete
  cleanly with the post-PR-#136/#150 codebase.
- **Bug discovery**: real-data runs surfaced three production bugs the
  synthetic-mode tests missed (fitness `_extreme_outlier_frac` counted
  NaN as outlier; handler passed raw universe name to `DataHandlerLP`;
  MultiIndex level order mismatched qlib's `StaticDataLoader`). All
  three fixed in PR #136 with explicit regression tests.
- **Operational ergonomics**: `pool_top_k` truncation (PR #150) defused
  both the Windows `[Errno 22]` multiprocessing crash and LightGBM
  overfit at high feature/sample ratios. Mining + bake-off now
  finishes overnight on a single workstation.
- **Direction of GP improvement is monotonic**: every fitness softening
  / truncation step moved OOS IR toward Alpha158's number. The control
  knobs do what they say.

## What did not work

- **§5.1 default fitness weights** are misconfigured for this data:
  `w_corr=0.8` crowds out signal-strong candidates.
- **OHLCV-only feature universe** is too narrow to compete with
  Alpha158's hand-engineered 158-feature library. The expressivity
  gap shows up directly in the top-pool repetition pattern (3
  semantic templates × variants).
- **Grammar permits pseudo-signal templates** like
  `ts_corr(f(X), X, N)` where `f` is monotonic — these score high in
  IS-IC but have no economic mechanism and don't transfer to OOS.

## Concrete follow-ups (ranked by expected impact)

1. **Extend the feature universe with `daily_basic` PIT fields** —
   PE / PB / PS / turnover_rate / float_share / total_share /
   circ_mv / pe_ttm. This adds the fundamental and microstructure
   signals that Alpha158 has and MinedFactor currently lacks. Concretely:
   - Add a `daily_basic` endpoint to the Tushare fetcher (`src/data/tushare/fetcher.py`).
   - Extend `QlibBinBuilder` to emit per-field bins under
     `features/<ticker>/<field>.day.bin`.
   - Extend `FeatureRegistry.V1` in `src/factor_mining/grammar.py` to
     register the new terminals (with appropriate `taint` flags).
   - Re-run the GP. **Expected effect: largest single move on OOS IC.**
2. **Grammar-level reject `op(f(X), X)` where `f` is monotonic** —
   currently mining picks up `ts_corr(log_safe($close), $close, N)`
   as a "high-IC" factor because the correlation is mechanically near
   1. A small grammar rule that rejects these at construction time
   would clean the pool. Sketch: deny `ts_corr(a, b)` and `ts_cov(a, b)`
   when one argument is a leaf and the other is a monotonic-univariate
   function of the same leaf (`log_safe`, `sqrt_safe`, `neg`, `abs`,
   `sign` of the same Terminal).
3. **Update `FitnessConfig` defaults** to the soft-pool values
   (`w_corr=0.1`, `w_turnover=0.05`, `cost_rate=0.001`,
   `w_complexity=0.005`). The §5.1 defaults from the design doc were
   set before any empirical run; the soft values are what produced
   the best OOS numbers in this evaluation.
4. **Try larger GP budget** (pop=500, gen=50) — 6.25× more evaluations.
   Speculative whether it helps before #1 lands; if expressivity is
   the bottleneck, more search of a narrow space won't move the IC
   meaningfully.
5. **Try a different universe** — csi500 or csi800. csi300 is a
   curated "large-cap value" pool where the factor edge is harder to
   find; mid-cap universes may have stronger inefficiencies for GP
   to exploit.

Recommendation: ship #2 (grammar reject) and #3 (default config
update) as the immediate follow-up PRs, both because they're cheap
and they're directly justified by the data above. #1 is the
materially-bigger change but requires a Tushare ingest extension and
a PIT-bundle rebuild; it's worth a separate scoped epic.

## Should we promote a v1 pool from this evaluation?

**Recommendation: no, not yet.** The promote CLI is a D4-manual gate
specifically because we want a human to look at the numbers before
shipping. Here the numbers say:
- The candidate pool's OOS IR is negative (-0.094 even for the best
  variant).
- The top-IS factors include pseudo-signals (`ts_corr` artefacts).
- Promoting a pool whose top 3 factors are mathematical artefacts
  would put noise into production that future operators might trust
  if they don't read this document.

A defensible v1 would come from a pool that (a) clears §10's IR
threshold and (b) survives a quick manual review of its top
expressions for economic mechanism. Neither holds today.

## Reproducibility

Run artefacts on disk (this workstation):

- `output/walk_forward_pit_full/walk_forward_report.json` — Alpha158 baseline (22/23 valid folds, IR=+0.466)
- `output/walk_forward_mined_pit_full/walk_forward_report.json` — default (18/23 valid, IR=-0.304)
- `output/walk_forward_mined_pit_full_soft/walk_forward_report.json` — soft (18/23 valid, IR=-0.126)
- `output/walk_forward_mined_pit_full_top20/walk_forward_report.json` — soft-top-20 (16/23 valid, IR=-0.094)
- `output/walk_forward_compare/pit_full_compare.json` — Alpha158 vs default
- `output/walk_forward_compare/pit_full_soft_compare.json` — Alpha158 vs soft
- `output/walk_forward_compare/pit_full_top20_compare.json` — Alpha158 vs soft-top-20
- `research/mined_factors/runs/pit_csi300_2018_2023_full_top50/` — default pool + GP history + config snapshot
- `research/mined_factors/runs/pit_csi300_2018_2023_full_top50_soft/` — soft pool
- `research/mined_factors/runs/pit_csi300_2018_2023_full_top20_soft/` — top-20 truncation of soft pool

Reproducibility commands are pinned in the per-run `config.yaml` snapshot. None of these artefacts are git-tracked — they live on the operator workstation. The configs that produced them (`config/factor_mining/pit_full*.yaml` and `config_walk_*_pit_full*.yaml`) are also operator-local; the source-controlled equivalents are `config/factor_mining/default.yaml` and `config_walk.yaml`, which document the same parameters with `OPERATOR-FILL` placeholders for PIT paths.

---

## Iteration 5: 12-feature universe (post-`extend-feature-universe-with-daily-basic`)

The single highest-ROI follow-up identified in iterations 1-4 was
"extend the feature universe with `daily_basic` fundamentals (PE/PB/PS/
turnover/cap)" — the proposal was authored in PR #182 and shipped via
PRs #184/185/187 (Tushare ingest + qlib bin builder + grammar
extension). FeatureRegistry.V1 went 6 → 12 terminals. This iteration
runs the bake-off on the extended universe.

### Configuration

- `data_basic` ingest 2018-2025 succeeded — 46776 parquet files, ~8.7M rows.
- qlib bundle rebuilt, calendar still 1942 days (2018-01-02 → 2025-12-31).
- Operator gotcha discovered along the way: `05_build_qlib_bins.py`
  silently wipes `instruments/csi300.txt` / `csi500.txt` / `csi800.txt`
  during rebuild (it only re-emits `all.txt`). Operators MUST re-run
  `03_resolve_index_membership.py` + `04_build_universe_files.py`
  + the SH000300 backfill helper after every bin rebuild. The new
  playbook script `scripts/operator_helpers/run_12feat_playbook.sh`
  (operator-local, not tracked) sequences these correctly.
- Two GP attempts at `pop=200/100` with `w_corr=0.1` (the soft-iteration
  setting from iterations 2-4) **both hit `MemoryError`** inside
  pandas's `_within_generation_novelty` MultiIndex stack despite 14
  GB free RAM — the 12-feature panel's heap-allocation pattern in
  `stack_v3(...)` fragments Python's allocator badly enough that even
  1.46 MiB allocations fail.
- **Fix shipped in this iteration's PR**: the engine short-circuits the
  novelty cache write + read when `w_corr == 0`. Operators opting
  out of novelty pressure (as the soft recipe does in spirit) now
  pay zero memory for the term.
- The successful run used `pop=100, gen=20, w_corr=0` (novelty
  pressure disabled, smaller population).

### Headline numbers

| Metric | Alpha158 baseline | **MinedFactor 12-feat (w_corr=0)** | vs iter-4 best |
|---|---:|---:|---:|
| mean_information_ratio | +0.466 | **-0.624** | -0.094 → **-0.624** (worse) |
| mean_ic_1d | +0.0247 | +0.0026 | +0.006 → +0.003 (slightly worse) |
| mean_annualized_return | +4.90% | -3.73% | +0.11% → -3.73% (worse) |
| worst_drawdown | -12.14% | -9.86% | -7.04% → -9.86% (worse) |
| `valid_folds_ic_1d` | 22 / 23 | 18 / 23 | (similar) |
| `design_doc_ir_threshold_met` | — | **FALSE** | — |

The expressivity expansion did NOT close the gap — in fact it
regressed across every metric vs the iter-4 soft-top-20 best.

### Root cause: novelty-off + extra features → template collapse

The pool inspection tells the story:

- **40 of 50 saved factors USE at least one daily_basic terminal** —
  the grammar extension worked, GP did adopt fundamentals.
- **But IS `|IC|` median = 0.0095** vs iter-4 soft pool's 0.017 —
  signal strength actually went DOWN.
- **Top expressions are all variants of `cs_*(ts_delta($turnover_rate, N))`**:

  ```
   #  fitness  IS-IC   expression
   1  +0.029  +0.011  cs_winsorize(ts_delta($turnover_rate, 20))
   2  +0.028  +0.010  cs_rank(ts_delta($turnover_rate, 20))
   3  +0.027  +0.010  cs_rank(ts_delta($turnover_rate, 10))
   4  +0.026  +0.012  cs_rank(div_safe(ts_delta($turnover_rate, 20), $total_mv))
   5  +0.025  +0.012  cs_rank(div_safe(ts_rank($close, 60), $turnover_rate))
   ```

Without novelty pressure, the GP converged on a single semantic
template ("turnover acceleration") and spent its search budget on
near-duplicates. The 50-factor pool is essentially the same factor
repeated 50 times with parameter variations. LightGBM with 50
highly-correlated features overfits to the noise in their differences
rather than learning genuine signal from feature diversity.

**Lesson: novelty pressure was load-bearing for diversity. Removing it
to dodge a memory bug cost us more than the bug would have.** The
fix going forward is to keep `w_corr > 0` and solve the memory
problem differently (smaller `pop`, or a streaming novelty
computation that doesn't allocate full MultiIndex Series).

### Cross-iteration summary

| Iteration | Recipe | OOS IR | OOS IC | Notes |
|---|---|---:|---:|---|
| 1 | 6-feat default (`w_corr=0.8`) | -0.304 | -0.002 | novelty crowds out signal |
| 2 | 6-feat soft (`w_corr=0.1`) | -0.126 | +0.003 | softer fitness, marginally better |
| 3 | 6-feat soft top-20 | **-0.094** | +0.006 | iter-4 best of the 6-feat runs |
| 4 | 12-feat soft pop=200 | OOM | OOM | pandas heap fragmented |
| 5 | **12-feat nocorr pop=100** | -0.624 | +0.003 | template collapse — diversity matters |
| (Alpha158 baseline, unchanged) | hand-engineered 158 | **+0.466** | **+0.025** | — |

After five iterations across two fitness variants × two feature
universes × three pool-size knobs, no recipe meets the design doc §10
IR threshold on csi300 2018-2025. The infrastructure works end-to-end;
the GP-on-OHLCV+daily_basic recipe does not beat 158 hand-engineered
features on this universe / window.

### Recommendation: close empirical experiments, open next-epic backlog

The remaining high-ROI directions all require architectural changes
beyond the iteration loop:

1. **`cs_industry_*` operator family** — industry-bucketed
   cross-sectional rank/zscore/demean. Alpha158's hand-engineered
   features implicitly carry industry-mix proxies; explicit industry
   neutralization at the GP layer would close part of that gap.
   Requires the industry taxonomy artefact (already built in another
   workstream) to be reachable from `src/factor_mining/`.
2. **Multi-objective GP** (NSGA-II flavour) — directly optimise the
   IR/drawdown/diversity Pareto front instead of bolting penalties
   onto a scalar fitness. Plausibly closes the
   novelty-vs-memory dilemma surfaced in iteration 5.
3. **Different universes** — csi500 / csi800 mid-cap pools have richer
   inefficiency than csi300 large-caps. The 12-feature recipe might
   land favourably there. Cheap to validate: the bundle already
   supports csi500/csi800 universe names.
4. **Linear model swap** — LightGBM on 50 correlated features is
   structurally bad. Ridge regression or simple weighted average
   would absorb the redundancy better. Single-line change at the
   walk-forward feature_handler / model side.

Each of these is a multi-day epic, not an iteration tweak. The
factor-mining subsystem itself is feature-complete (Phase 1-6 + 4
hot-fix/optimisation PRs + the daily_basic extension + this novelty
short-circuit). Empirical work to clear §10 is now a stack of
follow-up epics, each independently scoped, to be picked up by future
dispatch sessions.

### Iteration-5 reproducibility

- `output/walk_forward_mined_pit_full_12feat/walk_forward_report.json` —
  candidate (18/23 valid, IR = -0.624)
- `output/walk_forward_compare/pit_full_12feat_compare.json` —
  Alpha158 vs MinedFactor-12feat
- `research/mined_factors/runs/pit_csi300_2018_2023_12feat_nocorr_top50/` —
  pool + GP history + config snapshot

The Alpha158 baseline report (`output/walk_forward_pit_full/...`) is
unchanged from iteration 1 — the bundle rebuild that added
daily_basic bins does NOT touch the OHLCV bins Alpha158 reads, so
the baseline is still apples-to-apples.
