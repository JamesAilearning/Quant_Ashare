# Regression baselines

The canonical walk-forward baseline is **REGEN-2** (total-return, SH000300TR),
promoted to the root `walk_forward_baseline_metrics.json` in PR-2. **REGEN-A**
(price index, SH000300) is preserved as a control. The artifacts here are all
about the walk-forward headline.

## 1. Deterministic frozen-score replay — the PRIMARY anchors

### 1a. REGEN-2 CI-real — `test_walk_forward_replay_baseline_regen2` (canonical, CI-REAL)

The canonical anchor, and it **runs in CI for real** (not RUN_E2E-gated). It
replays the **23 frozen REGEN-2 per-fold Series** (`regen2/frozen_fold_scores.pkl.gz`)
through the canonical `BacktestRunner` (T+1, close-derived limits, PIT ST exclusion,
**SH000300TR total-return** benchmark) against a committed byte-identical mini-bundle
(`regen2_minibundle.tar.gz` + `.sha256`, checksum-verified before use), and asserts
the aggregate AND every per-fold metric reproduce the root
`walk_forward_baseline_metrics.json` within `REPLAY_ABS_TOL = 1e-6` (held in test
source, so a tampered fixture cannot widen its own gate).

Reproduction is byte-identity ON THE CANONICAL DEPENDENCY STACK (`numpy<2`,
`scipy<1.14`, `pandas<2.3` — the pyproject pin CI runs). The baseline is GENERATED
on that stack (a gen-env==canonical assertion fails generation off-pin). **fold-0's
frozen scores are degenerate** (~39 value-buckets / 261 ties at the topk cutoff), so
its selection — and thus its excess — depends on numpy's sort tie-break across numpy
MAJORS; CI is pinned to the canonical leg (ubuntu-3.12, numpy<2). Folds 1..22 are
numpy-version-insensitive. See `docs/baseline_regen2.md`.

Regenerate (canonical numpy<2 venv only):

```
python scripts/regen/replay_frozen_baseline_regen2.py \
  --provider-uri <unpacked regen2_minibundle> \
  --namechange-path <unpacked regen2_minibundle>/all_namechanges.parquet
```

### 1b. REGEN-A price control — `test_walk_forward_replay_baseline` (RUN_E2E)

Preserved as the SH000300 PRICE-index control. Replays the C1 round's 22 frozen
per-fold Series (`regen_a/frozen_fold_scores.pkl.gz`) and reproduces the control
fixture `regen_a/walk_forward_baseline_metrics_regen_a.json`. RUN_E2E-gated (needs
the real bundle; not CI-runnable). Regenerate:

```
RUN_E2E=1 python scripts/regen/replay_frozen_baseline.py \
  --provider-uri D:/qlib_data/my_cn_data_pit \
  --namechange-path D:/qlib_data/tushare_raw/all_namechanges.parquet
```

## 2. Value + framing pin — `test_regen_baseline_value_pin` (CI-runnable)

Runs in the fast suite (reads the root JSON only, no bundle). Pins that the committed
headline sits in a two-sided band `0.20 < IR < 0.35` (brackets the REGEN-2 canonical
mean fold IR ~0.28; EXCLUDES REGEN-A 0.48, old-T2 0.37, the off-pin ② figure 0.16, and
0), and that the mandated framing is committed with the number — corrected semantics,
the statistical caveat (within noise / not predictive of live), the **applied**
total-return (SH000300TR) basis, the per-fold block (≥23), and the fold-0
degenerate-tie-break known-limitation. So the headline can never be read without
context, and a regeneration on the wrong numpy major (→ 0.16, off-pin) is caught.

`test_canonical_benchmark_default_consistency` is the companion machine guard: the
canonical default benchmark (in-code + every tracked config YAML) is SH000300TR, the
REGEN-A control stays SH000300, and the TR↔price pairing is intact.

## 3. Walk-forward retrain baseline — `test_walk_forward_aggregate_baseline` (FU-5)

Re-runs the FULL walk-forward (retrain, all folds) and asserts headline aggregates
stay within **±5%** of the root `walk_forward_baseline_metrics.json`. Retrain-based,
so its band stays loose to absorb GPU/retrain noise. RUN_E2E-gated.

## Why some fixtures are git-ignored, some committed

`walk_forward_baseline_metrics.json` (the REGEN-2 canonical root),
`regen2/frozen_fold_scores.pkl.gz`, `regen2_minibundle.tar.gz` (+`.sha256`), and
`regen_a/frozen_fold_scores.pkl.gz` + `regen_a/walk_forward_baseline_metrics_regen_a.json`
(the price control) ARE committed — they are the deterministic anchors. They change
only on a deliberate, signed-off re-baseline ("I pull, you eyeball, you sign off, I
commit").

## When to refresh

Refresh (regenerate, eyeball, re-sign, commit in the SAME PR) whenever a merged PR
intentionally changes the canonical backtest semantics. Do NOT refresh because the
baseline is "slightly off" — that's the regression these tests exist to surface.

## Current baseline (committed) — REGEN-2 canonical (total-return)

Produced by **frozen-score replay** (NO retrain, NO bundle rebuild) of the REGEN-2
GPU fold scores through the canonical semantics with the **SH000300TR total-return**
benchmark, on the canonical numpy<2 stack. See `docs/baseline_regen2.md` for the full
analysis, the three-column lens, and the framing.

**Semantics:** T+1 execution (PR-C) + close-derived price limits (PR-D) + PIT ST
exclusion (PR-F). **Benchmark:** total-return `SH000300TR` (applied). SH000300 (price)
is the preserved REGEN-A control.

**Headline (23 real folds):**

| metric | REGEN-2 canonical (numpy<2) |
|---|---:|
| `mean_information_ratio` | **+0.278** |
| `mean_ic_1d` | +0.0181 |
| `mean_ic_5d` | +0.0284 |
| `mean_annualized_return` | +3.40% |
| `worst_drawdown` | −13.50% |

> ⚠ **The 0.28 is NOT robust signal strength.** It is INFLATED above the off-pin ②
> figure (0.16) ENTIRELY by fold-0's degenerate-score sort-tie-break artifact (IR
> −0.889 → +1.767 on the canonical stack), which also inflated the variance (mean-fold
> SE ≈ 0.43, t ≈ 0.65, 95% CI [−0.59, 1.04] straddles zero). The edge stays **unproven,
> not disproven**; one fold flipping does not change the SE-dominated picture. fold-0's
> degeneracy is PRE-EXISTING (REGEN-A's fold-0 is identically degenerate) and filed to
> phase-6. The honest edge is ~0.16–0.20. See `docs/baseline_regen2.md`.
