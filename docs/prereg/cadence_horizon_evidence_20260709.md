# 阶段7b cadence×horizon — full adjudication evidence (ruler machinery, seed 42, n_boot 10000, acf-decay block)
# Generated 2026-07-09; arms output/stage7/{daily_h1,daily_h5,weekly_h1,weekly_h5}, all @ commit 74f2c50, ST-off, 1397 OOS days.
# Reproduce: scripts/compare_walk_forward_runs.py + compare_paired_slices.py against the four arm run dirs.

## Per-arm net excess vs zero (one-sample moving-block bootstrap)
| arm | net_ann | 95% CI | state |
|---|---|---|---|
| daily_h1 | +0.0299 | [-0.0551, +0.1155] | indistinguishable-from-0 |
| daily_h5 | +0.0228 | [-0.0597, +0.1059] | indistinguishable-from-0 |
| weekly_h1 | +0.0351 | [-0.0504, +0.1209] | indistinguishable-from-0 |
| weekly_h5 | +0.0432 | [-0.0417, +0.1271] | indistinguishable-from-0 |

## 2x2 factorial: registered cells vs baseline daily_h1 (paired net + gross)
| comparison | dNet [95% CI] | net state | dGross [95% CI] | gross state |
|---|---|---|---|---|
| weekly_h5 vs daily_h1 (PRIMARY) | +0.0133 [-0.0436, +0.0696] | indistinguishable | -0.0388 [-0.0955, +0.0177] | indistinguishable |
| weekly_h1 vs daily_h1 (cadence@H1) | +0.0052 [-0.0458, +0.0568] | indistinguishable | -0.0471 [-0.0982, +0.0045] | indistinguishable |
| daily_h5 vs daily_h1 (horizon@daily) | -0.0071 [-0.0580, +0.0444] | indistinguishable | -0.0070 [-0.0580, +0.0444] | indistinguishable |

## PRIMARY comparison sensitivity slices (weekly_h5 vs daily_h1, net + gross)
| slice | n | paired NET diff [95% CI] | state | paired GROSS diff [95% CI] | state |
|---|---|---|---|---|---|
| FULL | 1397 | +0.0133 [-0.0436, +0.0696] | indistinguishable | -0.0388 [-0.0955, +0.0177] | indistinguishable |
| ex-fold0(2020Q2) | 1338 | +0.0043 [-0.0566, +0.0635] | indistinguishable | -0.0476 [-0.1089, +0.0115] | indistinguishable |
| ex-2020H2 | 1271 | +0.0025 [-0.0588, +0.0624] | indistinguishable | -0.0491 [-0.1106, +0.0105] | indistinguishable |
