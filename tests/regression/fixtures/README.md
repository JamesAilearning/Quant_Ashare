# Regression baselines

Two regression tests live in this directory; both are E2E-gated
(`RUN_E2E=1`) and both skip silently when their fixture files are
absent.

## 1. Fold-0 backtest baseline — `test_fold0_baseline`

Re-runs `BacktestRunner.run` against a frozen fold-0 prediction
fixture and asserts headline backtest metrics haven't drifted.

Generate the two fixtures:

1. Run a known-good walk-forward pipeline (e.g. `config_walk.yaml`
   with `ensemble_window=1`):
   ```
   python scripts/run_walk_forward.py config_walk.yaml
   ```

2. Copy the fold-0 predictions artifact:
   ```
   cp output/walk_forward/fold_00_predictions.pkl \
      tests/regression/fixtures/fold0_predictions.pkl
   ```

3. Extract the expected metrics from
   `output/walk_forward/fold_00_report.json` into
   `tests/regression/fixtures/fold0_expected_metrics.json`
   (see the test for the expected schema).

## 2. Walk-forward aggregate baseline — `test_walk_forward_aggregate_baseline` (FU-5)

Re-runs the FULL walk-forward (all folds, ensemble, aggregate) and
asserts headline aggregate metrics (mean IR / IC / annualized
return / worst drawdown) stay within **±5%** of the stored baseline.

Generate the baseline:

```
RUN_E2E=1 python scripts/generate_regression_baseline.py [config.yaml]
```

The script writes `walk_forward_baseline_metrics.json` here. Per
the project's reference-data workflow:

> I pull, you eyeball, you sign off, I commit.

So the script **does NOT auto-commit** the baseline. Operators must
open the JSON, sanity-check the headline numbers against expected
ranges, then `git add` + `git commit`.

Optionally drop a copy of the config used to generate the baseline
at `walk_forward_baseline_config.yaml` next to the metrics fixture
— the test prefers it over `config_walk.yaml` at the project root,
which means a baseline and its config can't drift apart.

## Why fixtures are git-ignored by default

`fold0_predictions.pkl` contains real predictions from production
runs; `walk_forward_baseline_metrics.json` carries metrics tied to a
specific bundle vintage. Both are useful regression anchors but
neither is appropriate for unconditional inclusion in the repo —
they grow stale on every bundle refresh. The reference-data
workflow keeps them under operator control.

## When to refresh

Refresh whenever:

- A merged PR intentionally changes the headline metric (e.g. PR1
  fixed the rank-IC double-counting; pre-PR1 baselines are
  no longer comparable).
- The qlib bundle is re-ingested with a new coverage window.
- Tushare publishes corrected historical data (rare).

Do NOT refresh because the baseline is "slightly off" — that's
exactly the regression these tests exist to surface.

## Current baseline (committed)

`walk_forward_baseline_metrics.json` + `walk_forward_baseline_config.yaml`
landed for the first time as the post-embargo-gap-fix clean reference.

**Provenance**

- Phase: **C1 — clean walk-forward baseline after embargo-gap fix.**
  See `docs/phase_c1_result.md` for the full investigation, per-fold
  table, and known-issue list.
- Reproducing main commit: **`bf4672b` (#212 — `fix: walk-forward
  embargo gap`)** or any newer main. Earlier commits cannot reproduce
  these numbers — the embargo-gap fix changed how WF generates fold
  windows.
- Bundle vintage: **PIT bundle (`D:/qlib_data/my_cn_data_pit`),
  calendar 2018-01-02 → 2025-12-31.** Refreshing the bundle past
  2025-12-31 is a refresh trigger (see "When to refresh" above).
- Run date: 2026-06-01.
- Config (resolved key params, for human read — full extends file in
  `walk_forward_baseline_config.yaml`):
  - `provider_uri: D:/qlib_data/my_cn_data_pit`
  - `instruments: csi300`, `feature_handler: Alpha158`
  - `overall_start: 2018-01-01`, `overall_end: 2025-12-31`
  - `train_months: 24`, `valid_months: 3`, `test_months: 3`, `step_months: 3`
  - `ensemble_window: 3`
  - `model_type: LGBModel`, `num_boost_round: 1000`,
    `early_stopping_rounds: 50`, `learning_rate: 0.005`
  - `topk: 50`, `n_drop: 5`, `signal_to_execution_lag: 1`
  - `benchmark_code: SH000300`
  - `compute_device: gpu` (the c1 run was GPU; CPU rerun should land
    within ±5% on the same bundle — LGB GPU vs CPU isn't bitwise
    identical but headline IC/IR drift well under tolerance)

**Headline numbers (over 22 valid folds — fold 22 excluded; see below)**

| metric | value |
|---|---:|
| `mean_information_ratio` | **+0.301** |
| `mean_ic_1d` | **+0.0224** |
| `mean_ic_5d` | **+0.0346** |
| `mean_annualized_return` | **+3.47%** |
| `worst_drawdown` | **-12.05%** |

**⚠ 22-of-23 folds — fold 22 deliberately excluded**

The 23rd fold (2025Q4 test window, 2025-10-01 → 2025-12-31) fails
during backtest with `IndexError: index 1942 is out of bounds for
axis 0 with size 1942` — its test window ends on the bundle's last
calendar day, and the backtest needs a T+1 execution bar that
doesn't exist. The aggregate metrics above are computed over the
**22 valid folds**; fold 22 is excluded. This is recorded in
`docs/phase_c1_result.md §7` and tracked as a separate fix outside
the scope of this fixture.

**When the fold-22 fix lands, this baseline WILL drift.** Most likely
outcomes once the backtest tolerates / pads the bundle tail:

- `num_folds` stays 23 but `valid_folds_*` becomes 23 (the
  previously-excluded fold contributes).
- `mean_information_ratio` / `mean_ic_1d` / `mean_annualized_return`
  / `worst_drawdown` shift by whatever 2025Q4 happens to add — the
  per-fold table in `docs/phase_c1_result.md` shows the surrounding
  folds vary widely, so adding one more is a real perturbation, not
  a rounding-noise drift.

That follow-up PR must:

1. Land its backtest tail-tolerance fix.
2. Regenerate this baseline via
   `RUN_E2E=1 python scripts/generate_regression_baseline.py
   tests/regression/fixtures/walk_forward_baseline_config.yaml`.
3. Eyeball the new headline numbers against the C1 22-fold reference
   above + the 2025Q4-fold-now-included expectation.
4. Commit the regenerated fixture **in the same PR** so the drift
   test stays green at merge time. Do NOT defer to a follow-up — a
   stale baseline + new code = false-positive regression for everyone
   downstream.

**Known follow-up issues recorded against this baseline** (NOT fixed
by this fixture commit; each gets its own PR):

- Fold-22 T+1 overrun (above).
- `engine.py:272` `_logger.info("  %s: %.4f", key, val)` raises
  `TypeError` for the `"timing"` aggregate key (dict). Logger swallows
  so the aggregate JSON itself is correct, but the AGGREGATE RESULTS
  log block prints half-truncated.
  See `docs/phase_c1_result.md §7`.

**What this fixture's `aggregate_metrics` dict drives**

`tests/regression/test_walk_forward_aggregate_baseline.py` calls
`compare_metrics(..., keys=_HEADLINE_METRICS)`, which restricts the
±5% drift check to exactly:

- `mean_information_ratio`
- `mean_ic_1d`
- `mean_ic_5d`
- `mean_annualized_return`
- `worst_drawdown`

Every other key in `aggregate_metrics` (`std_*`, `*_ci_low` /
`*_ci_high`, `valid_folds_*`, `bootstrap_seed`, `bootstrap_n`,
`num_folds`) is **recorded for human inspection only — NOT compared
by the drift test**. The fixture mirrors the full dict that
`scripts/generate_regression_baseline.py` writes so future
regenerations (e.g. after the fold-22 fix lands) diff cleanly. If a
later PR extends `_HEADLINE_METRICS`, the noise / "is this even a
metric" question — especially for `bootstrap_seed` (whose
`compare_metrics` docstring lines 71-75 already document as the
canonical NaN-silence example) and the noisier `std_*` / `*_ci_*`
moments — lands on that PR's review, not on this fixture's shape.
