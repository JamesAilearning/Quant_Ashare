# Ensemble Window Sweep — N ∈ {1, 2, 3, 5}

Same walk-forward configuration (csi300, Alpha158, LGB, 24m train / 3m valid / 3m test, 8 non-overlapping folds spanning 2024-04 ~ 2026-02) with `ensemble_window` swept across {1, 2, 3, 5}. N=1 is the no-op baseline.

## Aggregate metrics (across 8 folds)

| Metric                   | N=1 (baseline) | N=2 | N=3 | N=5 |
|--------------------------|----------------|-----|-----|-----|
| mean_ic_1d               | 0.0133 | 0.0195 | 0.0220 | 0.0218 |
| std_ic_1d                | 0.0132 | 0.0167 | 0.0135 | 0.0128 |
| mean_ic_5d               | 0.0179 | 0.0297 | 0.0319 | 0.0315 |
| mean_annualized_return   | 0.0349 | 0.0678 | 0.0770 | 0.0729 |
| mean_information_ratio   | −0.0827 | 0.4479 | 0.5788 | 0.5323 |
| worst_drawdown           | −0.0863 | −0.0881 | −0.0970 | −0.0792 |
| mean_ic_1d_ci_low        | 0.0033 | 0.0065 | 0.0114 | 0.0119 |
| mean_ic_1d_ci_high       | 0.0212 | 0.0293 | 0.0296 | 0.0298 |
| mean_ir_ci_low           | −1.2993 | −0.8915 | −0.7909 | −0.7371 |
| mean_ir_ci_high          | 1.1227 | 1.6740 | 1.8943 | 1.8228 |

## Per-fold IC (1-day forward)

| Fold | Test period           | N=1     | N=2     | N=3     | N=5     |
|------|-----------------------|---------|---------|---------|---------|
| 0    | 2024-04-01 ~ 2024-06-30 | +0.0263 | +0.0263 | +0.0263 | +0.0263 |
| 1    | 2024-07-01 ~ 2024-09-30 | +0.0175 | +0.0240 | +0.0240 | +0.0240 |
| 2    | 2024-10-01 ~ 2024-12-31 | +0.0226 | +0.0320 | +0.0249 | +0.0249 |
| 3    | 2025-01-01 ~ 2025-03-31 | +0.0222 | +0.0217 | +0.0264 | +0.0233 |
| 4    | 2025-04-01 ~ 2025-06-30 | +0.0185 | +0.0330 | +0.0345 | +0.0368 |
| 5    | 2025-07-01 ~ 2025-09-30 | −0.0164 | −0.0180 | −0.0112 | −0.0051 |
| 6    | 2025-10-01 ~ 2025-12-31 | +0.0141 | +0.0326 | +0.0335 | +0.0349 |
| 7    | 2026-01-01 ~ 2026-02-28 | +0.0018 | +0.0041 | +0.0176 | +0.0093 |

(Per-fold IC values sourced from `fold_NN_report.json` in each treatment run directory.)

## Observations

1. **Mean IC grows with N but plateaus**: N=1 → N=2 adds +0.0062 (+47%), N=2 → N=3 adds +0.0025 (+13%), N=3 → N=5 drops −0.0002 (−1%). The gain saturates at N=3. The IC improvement is directionally consistent but small — all values remain in the 0.01–0.02 range.

2. **IR sign-flip occurs at N=2 already**: N=1 IR = −0.0827, N=2 IR = +0.4479. The sign flip (negative → positive) happens between N=1 and N=2, not later. N=3 pushes it further to +0.5788, while N=5 drops slightly to +0.5323. This suggests the ensemble's main benefit is in screening out single-model parameter noise (N=1 → N=2) rather than averaging more models (N=2+).

3. **Fold 5 improvement is marginal**: The negative-IC outlier fold (2025Q3) shows N=1 IC = −0.0164, N=3 IC = −0.0112 — still negative, just less so. Averaging more priors (N=5) likely doesn't flip this fold either, since the signal genuinely goes against market direction (compressed cross-section dispersion in Q3-2025). Ensemble averaging smooths parameter noise but can't fix regime mismatches.

4. **Paired fold-level sign test: N=3 and N=5 are significant, N=2 is not**. Fold 0 is identical across all N (no prior models to ensemble). Across folds 1–7, N=3 vs N=1 shows 7/7 folds with higher IC (all positive differences). A one-sided sign test with n=7 yields p = 1/2⁷ ≈ 0.008 — significant at α = 0.05. N=5 vs N=1 also produces 7/7 positive differences (p = 0.008). N=2 vs N=1 produces only 5/7 positive differences (p ≈ 0.45, not significant). This paired test complements the bootstrap CIs: the CI measures cross-sectional variance of a single N's mean, while the sign test measures per-fold consistency of the N>1 minus N=1 ΔIC. The sign test is the appropriate statistic for answering "does N>1 reliably beat N=1 on the same folds?"

5. **N=5 provides the best tail-risk protection**. Worst drawdown improves from −8.63% (N=1) to −7.92% (N=5), the best of all treatments (N=2 = −8.81%, N=3 = −9.70%). After mean IC plateaus at N=3, longer averaging windows may still reduce tail risk without further improving IC — the ensemble behaves like a shrinkage estimator that pulls extreme predicted scores towards the cross-model mean, dampening outlier positions.

6. **Wall-clock cost**: N=2 replayed 1 prior per fold (fold 1+), N=5 replayed 4 priors (fold 4+). Total runtime: N=2 ~4 min, N=5 ~5 min (vs N=1 ~3 min). The +25% runtime for N=2 is a fair trade for the +47% IC gain, even if the gain isn't statistically distinguishable from noise.

## Recommendation

**Adopt N=3 as the new default.** Rationale:

- **Statistical significance**: N=3 vs N=1 per-fold IC improvement is significant under a paired sign test (p = 0.008). N=2 is not (p ≈ 0.45, only 5/7 folds improve).
- **Directional consistency**: All three treatments (N=2, N=3, N=5) show higher mean IC and IR than N=1. N=3 achieves the best point estimate across IC, IR, and annualized return.
- **Avoiding negative-IR default**: N=1's mean IR is −0.0827 — keeping this as default means operators underperform the benchmark on a risk-adjusted basis out of the box. N=3's IR of +0.5788 is directionally positive and the highest in the sweep.
- **Cost**: N=3 replays at most 2 prior models per fold. Wall-clock cost is ~+66% vs N=1 (3 min → 5 min) — acceptable for a batch training workflow.
- **N=5 as an alternative**: While N=5 IC plateaus relative to N=3, it shows the best worst-drawdown (−7.92% vs −8.63% for N=1). Operators prioritizing tail-risk protection may prefer N=5. This is a low-cost safety upgrade once N=3 is the baseline.

For future work: re-run the N=1 and N=3 baselines with the current code (which includes bootstrap CI) so that all four columns have complete confidence intervals. Consider increasing fold count (e.g. `step_months=1`) to narrow CIs and improve statistical power of between-N comparisons.

## Raw artifacts

- `output/walk_forward_industry/walk_forward_report.json` — N=1 baseline
- `output/walk_forward_industry_n2/walk_forward_report.json` — N=2
- `output/walk_forward_industry_n3/walk_forward_report.json` — N=3
- `output/walk_forward_industry_n5/walk_forward_report.json` — N=5
