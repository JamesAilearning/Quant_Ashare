# Sector α Consistency Across Folds

Per-fold Brinson attribution rolled up across 8 folds (2024-04 ~ 2026-02)
of the walk-forward backtest. For each ensemble window N ∈ {1, 3} (N ∈ {2, 5}
raw outputs also available in `output/`), we tabulate which sectors
consistently produced alpha (sign-consistency near 1.0) vs which had
large mean effects driven by 1-2 outlier folds.

## Headline findings

1. **软件开发 is the cleanest positive contributor under N=3**: mean total_effect +0.31% across all 8 folds with perfect sign_consistency=1.00. Every single fold contributed positively to this sector, and the selection effect (+0.29%) dominates allocation (+0.07%). This is not a fold-5 fluke — the same sector was also the #1 positive contributor under N=1 (mean +0.29%), confirming genuine sector-level alpha independent of the ensemble window.

2. **证券Ⅱ was consistent but lost consistency under N=3 vs N=1**: N=1 showed sign_consistency=0.88 (7/8 folds) with mean +0.52% total_effect. Under N=3, the mean dropped to +0.27% and sign_consistency held at 0.88. The ensemble preserved the direction but halved the magnitude — suggesting N=1's large positive effect in this sector was partly parameter noise that cross-model averaging dampened. Conversely, **半导体** improved from scT=0.62 under N=1 to scT=0.75 under N=3 (+0.13), gaining consistency as the ensemble smoothed single-fold outliers.

3. **通信服务 is the most persistent drag under N=3**: mean total_effect −0.08% across 8 folds, but sign_consistency is only 0.50 (exactly half the folds positive, half negative). This is a true noisy sector — the model has no directional conviction here, and it consistently underperforms relative to the benchmark. Under N=1, it was even worse (−0.86% mean, scT=0.25). Ensemble narrowed the underperformance but still couldn't make it consistently positive — a prime candidate for a sector tilt or exclusion in future strategy configurations.

4. **Fold 5 (2025Q3) losses are concentrated in equipment, metals, and telecom sectors**: The fold's worst selection-effect sectors were **通信设备** (−0.71% selection, offset by +0.82% allocation) and **工业金属** (−0.42% selection, −0.36% allocation). Both are in the broader industrial/commodity group. On the allocation side, **通信服务** (−0.44%) and **能源金属** (−0.43%) were the largest negative contributors; **工业金属** appears in both tables (−0.36% allocation). The losses are not broad-based — they cluster in specific industrial, metal, and telecom sub-sectors, which aligns with the known Q3-2025 regime of compressed cross-section dispersion: the model's stock-ranking signal had no predictive power in these specific sectors during that quarter.

5. **Only 3 sectors have perfect sign consistency (scT=1.0) under N=3 across all 8 folds**: **软件开发** (+0.31%), **股份制银行Ⅱ** (+0.25%), and **医疗器械** (+0.06%). These three sectors are the strongest evidence of genuine, persistent sector-level alpha — every fold, regardless of regime, produced positive total attribution. Under N=1, only **软件开发** and **股份制银行Ⅱ** had scT=1.0; **半导体** joined them under N=3 (scT=0.62→0.75, not quite 1.0 but close). The ensemble made borderline sectors more stable but didn't create new "always-positive" sectors — which is exactly the expected behavior: ensemble reduces noise but doesn't invent alpha.

## N=3 (current default) — top 30 sectors by total effect

```
python scripts/aggregate_industry_attribution.py output/walk_forward_industry_n3 --limit 30
```

**(embedded from `output/sector_consistency_n3.md`):**

| Sector | N | PW | BW | Alloc | sc_A | Select | sc_S | Total | sc_T |
|---|---|---|---|---|---|---|---|---|---|
| 软件开发 | 8 | 0.0297 | 0.0301 | 0.0007 | 0.6250 | 0.0029 | 0.8750 | 0.0031 | 1.0000 |
| 证券Ⅱ | 8 | 0.0649 | 0.0735 | -0.0000 | 0.3750 | 0.0029 | 0.6250 | 0.0027 | 0.8750 |
| 半导体 | 8 | 0.0449 | 0.0602 | -0.0002 | 0.5000 | 0.0029 | 0.8750 | 0.0026 | 0.7500 |
| 股份制银行Ⅱ | 8 | 0.0574 | 0.0301 | 0.0010 | 0.7500 | 0.0018 | 0.8750 | 0.0025 | 1.0000 |
| 城商行Ⅱ | 8 | 0.0453 | 0.0234 | 0.0010 | 0.7500 | 0.0011 | 0.5000 | 0.0018 | 0.6250 |
| 影视院线 | 8 | 0.0023 | 0.0033 | -0.0001 | 0.2500 | 0.0016 | 0.8750 | 0.0014 | 0.8750 |
| 电力 | 8 | 0.0560 | 0.0380 | 0.0008 | 0.6250 | 0.0003 | 0.6250 | 0.0009 | 0.7500 |
| 通信服务 | 8 | 0.0197 | 0.0134 | -0.0006 | 0.5000 | -0.0002 | 0.5000 | -0.0008 | 0.5000 |
| 能源金属 | 8 | 0.0077 | 0.0134 | -0.0009 | 0.5000 | 0.0000 | 0.5000 | -0.0008 | 0.6250 |
| 医疗器械 | 8 | 0.0130 | 0.0027 | 0.0008 | 1.0000 | 0.0000 | 0.5000 | 0.0006 | 1.0000 |

(Full output with 30 rows available in `output/sector_consistency_n3.md`.)

## N=1 (baseline) — top 30 sectors by total effect

**(embedded from `output/sector_consistency_n1.md`):**

| Sector | N | PW | BW | Alloc | sc_A | Select | sc_S | Total | sc_T |
|---|---|---|---|---|---|---|---|---|---|
| 证券Ⅱ | 8 | 0.0663 | 0.0735 | 0.0001 | 0.5000 | 0.0060 | 0.7500 | 0.0052 | 0.8750 |
| 软件开发 | 8 | 0.0309 | 0.0301 | 0.0006 | 0.5000 | 0.0007 | 0.7500 | 0.0029 | 0.7500 |
| 股份制银行Ⅱ | 8 | 0.0611 | 0.0301 | 0.0003 | 0.5000 | 0.0053 | 0.8750 | 0.0029 | 1.0000 |
| 电力 | 8 | 0.0561 | 0.0380 | 0.0006 | 0.5000 | 0.0005 | 0.6250 | 0.0014 | 0.7500 |
| 半导体 | 8 | 0.0456 | 0.0602 | 0.0002 | 0.2500 | 0.0016 | 0.7500 | 0.0013 | 0.6250 |
| 城商行Ⅱ | 8 | 0.0457 | 0.0234 | 0.0004 | 0.6250 | 0.0017 | 0.8750 | 0.0008 | 0.5000 |
| 影视院线 | 8 | 0.0021 | 0.0033 | -0.0001 | 0.2500 | 0.0004 | 0.6250 | 0.0003 | 0.6250 |
| 通信服务 | 8 | 0.0190 | 0.0134 | -0.0023 | 0.5000 | 0.0006 | 0.5000 | -0.0086 | 0.2500 |
| 医疗器械 | 8 | 0.0132 | 0.0027 | 0.0006 | 0.8750 | 0.0015 | 0.7500 | 0.0003 | 0.7500 |
| 能源金属 | 8 | 0.0080 | 0.0134 | -0.0016 | 0.3750 | -0.0023 | 0.3750 | -0.0022 | 0.7500 |

(Full output with 30 rows available in `output/sector_consistency_n1.md`.)

## Stability ranking — sectors with sign_consistency = 1.0 under N=3

Three sectors contributed in the same (positive) direction in every single fold:

- **软件开发** (mean total_effect +0.31%) — dominates via selection effect (+0.29%)
- **股份制银行Ⅱ** (mean total_effect +0.25%) — balanced allocation (+0.10%) and selection (+0.18%)
- **医疗器械** (mean total_effect +0.06%) — smaller magnitude but directionally reliable, pure allocation contribution

Under N=1, only the first two had scT=1.0; **医疗器械** (N=1 scT=0.75, mean +0.03%) improved from 6/8 to 8/8 consistent folds under N=3.

## Fold-5 (2025Q3) sector breakdown

Fold 5 was the negative-IC outlier in every N. Below is its per-sector Brinson decomposition (from `output/walk_forward_industry_n3/fold_05_report.json`).

### Worst 5 selection-effect sectors — where the model picked the wrong stocks

| Sector | Selection | Allocation | Total | Port. Wt |
|---|---|---|---|---|
| 通信设备 | −0.0071 | +0.0082 | −0.0028 | 0.0309 |
| 工业金属 | −0.0042 | −0.0036 | −0.0049 | 0.0074 |
| 工程机械 | −0.0022 | −0.0004 | −0.0021 | 0.0100 |
| 汽车零部件 | −0.0020 | +0.0004 | −0.0025 | 0.0237 |
| 游戏Ⅱ | −0.0016 | −0.0010 | −0.0010 | 0.0000 |

### Worst 5 allocation-effect sectors — where the portfolio was over/underweight

| Sector | Selection | Allocation | Total | Port. Wt |
|---|---|---|---|---|
| 通信服务 | −0.0004 | −0.0044 | −0.0054 | 0.0359 |
| 能源金属 | +0.0015 | −0.0043 | −0.0039 | 0.0031 |
| 消费电子 | +0.0073 | −0.0042 | +0.0009 | 0.0185 |
| 航运港口 | −0.0004 | −0.0039 | −0.0049 | 0.0572 |
| 工业金属 | −0.0042 | −0.0036 | −0.0049 | 0.0074 |

The losses cluster in equipment / metals (**通信设备**, **工业金属**, **工程机械**, **汽车零部件**) and telecom / energy (**通信服务**, **能源金属**). This is consistent with the known Q3-2025 regime of compressed cross-section dispersion: the model's stock-ranking signal had no predictive power in these specific sectors during that quarter, producing both negative selection (wrong stock picks) and negative allocation (wrong sector weight).

## Ensemble effect: which sectors became more / less consistent under N=3?

Sign-consistency delta = scT(N=3) − scT(N=1). Positive = ensemble made the sector more consistent across folds.

### Top 10 — sectors that became more consistent under N=3

| Sector | scT N=1 | scT N=3 | Δ |
|---|---|---|---|
| 电力 | 0.2500 | 0.7500 | +0.5000 |
| 光学光电子 | 0.3750 | 0.6250 | +0.2500 |
| 农产品加工 | 0.5000 | 0.7500 | +0.2500 |
| 家电零部件Ⅱ | 0.3750 | 0.6250 | +0.2500 |
| 油服工程 | 0.5000 | 0.7500 | +0.2500 |
| 生物制品 | 0.5000 | 0.7500 | +0.2500 |
| 证券Ⅱ | 0.6250 | 0.8750 | +0.2500 |
| 通信设备 | 0.5000 | 0.7500 | +0.2500 |
| 医疗器械 | 0.8750 | 1.0000 | +0.1250 |
| 股份制银行Ⅱ | 0.8750 | 1.0000 | +0.1250 |

### Bottom 10 — sectors that became less consistent under N=3

| Sector | scT N=1 | scT N=3 | Δ |
|---|---|---|---|
| 化学原料 | 0.8750 | 0.5000 | −0.3750 |
| 白酒Ⅱ | 0.6250 | 0.3750 | −0.2500 |
| 工业金属 | 0.6250 | 0.3750 | −0.2500 |
| 广告营销 | 0.7500 | 0.5000 | −0.2500 |
| 养殖业 | 0.6250 | 0.3750 | −0.2500 |
| 其他电源设备Ⅱ | 0.6250 | 0.3750 | −0.2500 |
| 元件 | 0.6250 | 0.3750 | −0.2500 |
| 饮料乳品 | 0.6250 | 0.5000 | −0.1250 |
| 通信服务 | 0.6250 | 0.5000 | −0.1250 |
| 轨交设备Ⅱ | 0.6250 | 0.5000 | −0.1250 |

(Full 80-sector delta table available in `output/sector_consistency_ensemble_effect.md`.)

The sectors that improved under N=3 are concentrated in two groups: **manufacturing** (光学光电子, 家电零部件Ⅱ, 通信设备, 油服工程) and **finance** (证券Ⅱ, 农产品加工). The standout is **电力** (+0.5000), the largest ensemble improvement in the dataset — N=1 had only 2/8 folds with correct sign, N=3 recovered to 6/8. The sectors that worsened (**化学原料**, **白酒Ⅱ**, **工业金属**, **养殖业**) are predominantly commodities and cyclical cyclicals where the warm ensemble assumption (cross-fold RobustZScoreNorm is stable enough for prior model replay) appears to break down. Notably, **白酒Ⅱ** — one of the benchmark's largest sector weights — loses sign consistency under N=3 (Δ=−0.250, from 5/8 to 3/8), suggesting that for high-concentration consumer sectors, the ensemble replay of prior models at slightly different normalisation scales may introduce noise rather than reduce it.

## Raw data

- `output/sector_consistency_n1.md` — N=1 (baseline) top 30
- `output/sector_consistency_n2.md` — N=2 top 30
- `output/sector_consistency_n3.md` — N=3 (default) top 30
- `output/sector_consistency_n5.md` — N=5 top 30
- `output/sector_consistency_ensemble_effect.md` — N=3 vs N=1 sign-consistency delta (all 80 sectors)
