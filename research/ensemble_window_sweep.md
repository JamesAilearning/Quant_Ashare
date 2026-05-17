# Ensemble Window Sweep — N ∈ {1, 2, 3, 5}

Same walk-forward configuration (csi300, Alpha158, LGB, 24m train / 3m valid / 3m test, 8 non-overlapping folds spanning 2024-04 ~ 2026-02) with `ensemble_window` swept across {1, 2, 3, 5}. N=1 is the no-op baseline.

## Aggregate metrics (across 8 folds)

| Metric                   | N=1 (baseline) | N=2 | N=3 | N=5 |
|--------------------------|----------------|-----|-----|-----|
| mean_ic_1d               | 0.0133 | 0.0195 | 0.0220 | 0.0218 |
| mean_ic_5d               | — | 0.0297 | — | 0.0315 |
| mean_annualized_return   | 0.0349 | 0.0678 | 0.0770 | 0.0729 |
| mean_information_ratio   | −0.0827 | 0.4479 | 0.5788 | 0.5323 |
| worst_drawdown           | −0.0863 | −0.0881 | −0.0970 | −0.0792 |
| std_ic_1d                | — | 0.0167 | — | 0.0128 |
| mean_ic_1d_ci_low        | — | 0.0065 | — | 0.0119 |
| mean_ic_1d_ci_high       | — | 0.0293 | — | 0.0298 |
| mean_ir_ci_low           | — | −0.8915 | — | −0.7371 |
| mean_ir_ci_high          | — | 1.6740 | — | 1.8228 |

(CI columns only available for N=2 and N=5 — the bootstrap CI feature was added after N=1 and N=3 baseline runs. N=1 and N=3 CIs show as `[0.0000, 0.0000]` which is the fallback for old reports without the field.)

## Per-fold IC (1-day forward)

| Fold | Test period           | N=1     | N=2 | N=3     | N=5 |
|------|-----------------------|---------|-----|---------|-----|
| 0    | 2024-04-01 ~ 2024-06-30 | +0.0263 | | +0.0263 | |
| 1    | 2024-07-01 ~ 2024-09-30 | +0.0175 | | +0.0240 | |
| 2    | 2024-10-01 ~ 2024-12-31 | +0.0226 | | +0.0249 | |
| 3    | 2025-01-01 ~ 2025-03-31 | +0.0222 | | +0.0264 | |
| 4    | 2025-04-01 ~ 2025-06-30 | +0.0185 | | +0.0345 | |
| 5    | 2025-07-01 ~ 2025-09-30 | −0.0164 | | −0.0112 | |
| 6    | 2025-10-01 ~ 2025-12-31 | +0.0141 | | +0.0335 | |
| 7    | 2026-01-01 ~ 2026-02-28 | +0.0018 | | +0.0176 | |

(N=1 and N=3 values are from the existing comparison run; N=2 and N=5 per-fold values available in fold reports but not yet tabulated here — the aggregate metrics above are the headline.)

## Observations

1. **Mean IC grows with N but plateaus**: N=1 → N=2 adds +0.0062 (+47%), N=2 → N=3 adds +0.0025 (+13%), N=3 → N=5 drops −0.0002 (−1%). The gain saturates at N=3. The IC improvement is directionally consistent but small — all values remain in the 0.01–0.02 range.

2. **IR sign-flip occurs at N=2 already**: N=1 IR = −0.0827, N=2 IR = +0.4479. The sign flip (negative → positive) happens between N=1 and N=2, not later. N=3 pushes it further to +0.5788, while N=5 drops slightly to +0.5323. This suggests the ensemble's main benefit is in screening out single-model parameter noise (N=1 → N=2) rather than averaging more models (N=2+).

3. **Fold 5 improvement is marginal**: The negative-IC outlier fold (2025Q3) shows N=1 IC = −0.0164, N=3 IC = −0.0112 — still negative, just less so. Averaging more priors (N=5) likely doesn't flip this fold either, since the signal genuinely goes against market direction (compressed cross-section dispersion in Q3-2025). Ensemble averaging smooths parameter noise but can't fix regime mismatches.

4. **Bootstrap CIs: N=2 IC gain is statistically distinguishable, N=5 is tighter**: N=2 CI = [0.0065, 0.0293] excludes the N=1 mean of 0.0133 only on the upper side (0.0133 < 0.0293 but not < 0.0065 — actually 0.0133 is within [0.0065, 0.0293]). N=5 CI = [0.0119, 0.0298] is tighter (std drops from 0.0167 to 0.0128) and also contains N=1's mean. **No ensemble window produces an IC improvement that is statistically significant at 95% CI vs baseline.** The N=1 mean falls within every N>1 CI.

5. **Wall-clock cost**: N=2 replayed 1 prior per fold (fold 1+), N=5 replayed 4 priors (fold 4+). Total runtime: N=2 ~4 min, N=5 ~5 min (vs N=1 ~3 min). The +25% runtime for N=2 is a fair trade for the +47% IC gain, even if the gain isn't statistically distinguishable from noise.

## Recommendation

**Keep N=1 as the default.** The ensemble window sweep reveals that while N>1 consistently produces higher mean IC and IR, none of the improvements survive a 95% bootstrap CI that excludes the baseline mean. The IR sign-flip from −0.08 to +0.45 is directionally encouraging but the CI spans from −0.89 to +1.67 — too wide to act on with 8 folds.

For research/exploration: N=2 is the most cost-effective upgrade (+47% IC for +25% runtime). N=3 adds another +13% IC at the cost of replaying 2 priors each. N=5 adds no further IC benefit over N=3.

For future work: increase the number of folds (e.g. step_months=1) to narrow the bootstrap CIs. The current 8-fold design gives CIs too wide to distinguish ensemble gains from sampling noise.

## Raw artifacts

- `output/walk_forward_industry/walk_forward_report.json` — N=1 baseline
- `output/walk_forward_industry_n2/walk_forward_report.json` — N=2
- `output/walk_forward_industry_n3/walk_forward_report.json` — N=3
- `output/walk_forward_industry_n5/walk_forward_report.json` — N=5
