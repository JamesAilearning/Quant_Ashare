# 阶段 C1 结果 — 修 walk-forward embargo bug + 干净 23 折 baseline

> **worktree**:`D:/stock/worktrees/Quant_Ashare_phase_c1`(分支 `fix/walk-forward-embargo-gap`,基于 `origin/main` eeca734 / 含 #211)
> **范围**:只修 WF 折生成的 embargo gap;不动护栏、不调模型参数、不碰荐股/D3/配置。
> **状态**:Step 0(调查+提案)✅ ｜ Step 1(改引擎+红线测试)✅ ｜ Step 2(干净 23 折重跑)✅。未 commit/push。

---

## 1. Bug 根因

walk-forward baseline 在 main 上 **23 折全挂**(阶段A实测每折 0.0s、训练前即失败)。根因是两段逻辑相撞:

- `WalkForwardEngine._generate_windows()` 用纯日历月算术生成折,段间**日历相邻**:`valid_start = train_end + 1 天`、`test_start = valid_end + 1 天` → 段间 **0 个交易日**。
- Alpha158 label 前视护栏 `src/data/_segment_embargo.py`(`LABEL_LOOKAHEAD_DAYS = 2`,经 **#157(2026-05-25)** 接入 `FeatureDatasetBuilder.build`)要求段间 **≥2 个交易日**(label `Ref($close,-2)/Ref($close,-1)-1` 在 t 日读 t+1、t+2 价)。相邻边界 = 0 < 2 → 每折 build 前被护栏拒。

## 2. 修法 —— 加 gap,**绝不削弱护栏**

- **`_segment_embargo.py` 一字未改**(`git diff origin/main -- src/data/_segment_embargo.py` = 0 行)。没降 `LABEL_LOOKAHEAD_DAYS`、没跳过/绕过 `_validate_embargo`。
- 改的是**折生成**:`_generate_windows` 把 `train_end`、`valid_end` 用**交易日历往前缩**到留出 `LABEL_LOOKAHEAD_DAYS` 个交易日的 gap(`train_e = cal[idx_of(valid_s) - (gap+1)]`,valid_e 同理),丢弃的 gap 天**不进任何 segment**。
- **gap 大小从护栏常量 `LABEL_LOOKAHEAD_DAYS` 读,非写死** → 生成器与护栏不会漂移。
- 月对齐 start anchors(`valid_s`/`test_s`)和 `test_e` **不变**,保持季度网格 / 文档的 23 折布局(codex #211 P2 的月对齐得以保留)。只有 train/valid 段尾各缩约 2 个交易日。
- 交易日历经 `run()` 的可注入 `_load_trading_calendar()`(走 `D.calendar()`)传入;`run()` 已确保 qlib init。**实测该日历 = PIT bundle 日历**(`len=1942, 2018-01-02 → 2025-12-31`),gap 用的是 PIT 日历。

## 3. embargo gap 测试(`tests/logic/test_walk_forward_embargo_gap.py`,7 项,纯合成日历无 qlib)

- **无前视泄露红线**(核心):对每折,train 末行 label 读的 `{train_end+1 … train_end+LABEL_LOOKAHEAD_DAYS}` 交易日与 `[valid_start, valid_end]` **交集为空**(`test_no_label_lookahead_leak_into_valid` ✅);valid→test 同理(`test_same_no_leak_for_valid_into_test` ✅)。
- **两边界都覆盖**:用护栏自己的 `validate_segment_embargo` 当 oracle,每折在 train→valid 和 valid→test **两个边界**都返回 `[]`(`test_every_fold_accepted_by_guard_on_both_boundaries` ✅)。
- **gap = 常量**:`trading_days_between` 实测恰好 = `LABEL_LOOKAHEAD_DAYS`(若有人写死别的数即漂移失败)。
- anchors 月对齐保持;首折 test = 2020-04-01(匹配 empirical)。
- 全量 `pytest tests/logic tests/governance` = **2110 passed / 25 skipped / 0 failed**(我改 `_generate_windows` 行为打破的 6 个现有 WF 测试已如实修复:折生成测试改传合成日历+gap 断言、run 测试注入 `_load_trading_calendar`)。

## 4. 干净 23 折滚动 baseline(PIT + Alpha158 + LGB GPU)

**fix 在真实跑中生效**:23 折每折真训练(Best iteration 5~470,各折 ~36s),**不再整折 embargo 失败**。22 折成功 + fold 22 失败(见 §6,日历末尾越界,非 embargo)。

逐折(test 期 / RankIC(1d) / IR / 年化 / 回撤):

| fold | test | RankIC | IR | 年化 | MaxDD |
|---|---|---:|---:|---:|---:|
| 0 | 2020Q2 | 0.0227 | 0.33 | +2.3% | -1.6% |
| 1 | 2020Q3 | 0.0145 | -2.03 | -24.0% | -5.8% |
| 2 | 2020Q4 | 0.0378 | -1.40 | -15.2% | -6.3% |
| 3 | 2021Q1 | 0.0019 | 1.00 | +10.3% | -3.2% |
| 4 | 2021Q2 | 0.0255 | 3.40 | +33.7% | -1.8% |
| 5 | 2021Q3 | -0.0334 | -0.13 | -1.7% | -7.4% |
| 6 | 2021Q4 | -0.0102 | 0.69 | +5.3% | -1.7% |
| 7 | 2022Q1 | -0.0012 | -0.61 | -6.3% | -6.6% |
| 8 | 2022Q2 | 0.0232 | 3.04 | +37.1% | -4.2% |
| 9 | 2022Q3 | -0.0072 | -0.08 | -0.8% | -5.7% |
| 10 | 2022Q4 | 0.0415 | 0.97 | +9.7% | -5.2% |
| 11 | 2023Q1 | 0.0352 | -1.75 | -11.4% | -3.3% |
| 12 | 2023Q2 | 0.0076 | 0.62 | +4.1% | -2.4% |
| 13 | 2023Q3 | 0.0362 | -2.63 | -16.1% | -4.6% |
| 14 | 2023Q4 | 0.0277 | 0.99 | +6.9% | -3.3% |
| 15 | 2024Q1 | 0.0746 | 1.81 | +18.8% | -3.8% |
| 16 | 2024Q2 | 0.0590 | 2.12 | +14.7% | -1.3% |
| 17 | 2024Q3 | 0.0415 | 0.54 | +3.9% | -3.4% |
| 18 | 2024Q4 | 0.0275 | 0.84 | +17.5% | -3.1% |
| 19 | 2025Q1 | 0.0190 | 0.88 | +8.7% | -3.9% |
| 20 | 2025Q2 | 0.0472 | 2.59 | +21.6% | -2.3% |
| 21 | 2025Q3 | 0.0014 | -4.55 | -42.6% | -12.1% |
| 22 | 2025Q4 | — | — | FAILED | — |

**汇总(22 有效折)**:
- mean RankIC(1d) = **+0.0224**(std 大,逐折 -0.033~+0.075)
- mean IR = **+0.301**
- mean 年化 = **+3.47%**
- worst drawdown = **-12.05%**(fold 21)
- valid_folds = **22 / 23**

## 5. GP vs Alpha158 对比 —— 本阶段跳过(说明)

未跑。原因:(a) GP miner 的 `config/factor_mining/default.yaml` 在 main 上 `load_config` 即崩(`features` 死键,阶段A任务3发现,未修);(b) 一次 GP(pop×gen=4000 evals)耗时远超本阶段预算。
**更重要的是公平性**:要复核"GP vs Alpha158",GP 必须在**同样加了 embargo gap 的干净折**上重跑(经修好的 WF + MinedFactor handler),否则又是泄露对泄露。这属于后续工作,前置依赖:修 default.yaml 死键。

## 6. 旧 empirical 是否可信 —— 结论:**不可信,需用本 baseline 复核**

`docs/factor_mining/empirical_results_b_std.md` 的 Alpha158 baseline vs 本阶段干净 baseline:

| 指标 | 旧 empirical | **C1 干净(加 gap)** | 解读 |
|---|---:|---:|---|
| valid_folds | 22/23 | 22/23 | 同 |
| mean RankIC(1d) | +0.0247 | +0.0224 | 接近,略低 |
| **mean IR** | **+0.466** | **+0.301** | **旧值明显虚高** |
| mean 年化 | +4.90% | +3.47% | 旧值偏高 |
| worst drawdown | -12.14% | -12.05% | 同 |

**判定(Step 0 调查 + Step 2 实证一致)**:
- `_validate_embargo` 进 `build` 的唯一 commit 是 **#157(05-25)**,在 empirical 文档(#180/#200, 05-27/29)之前;而当前"相邻折 + 护栏"组合下 empirical **不可能复现**(会全挂)。所以那份 23 折**必然产于 #157 把护栏接入 build 之前的相邻折状态 → 带 train→valid label 前视泄露**。
- 实证佐证:加上 embargo gap(消除泄露)后,**IR 从 0.466 掉到 0.301**(年化 4.90%→3.47%)。前视泄露正是把 IR/收益抬高的来源,RankIC/回撤受影响小,与"label 偷看下一段头部"的机理吻合。
- ⇒ 旧 empirical 的绝对数字、以及其上得出的"**GP 跑输 Alpha158**"结论**不可信**。干净的 Alpha158 baseline 是 **IR ≈ 0.30**(不是 0.47)。GP 需在干净折上重跑后才能下结论。

## 7. 已知问题(均非 embargo fix 范围,记账)

1. **fold 22 失败 — 回测到 PIT 日历末尾越界**:`BacktestRunnerError: index 1942 is out of bounds for axis 0 with size 1942`。fold 22 test 期 2025-10-01~2025-12-31,`test_e` = PIT 日历最后一天,回测需 T+1 执行 bar → 越界。**与阶段B单折踩的是同一个边界 bug**(回测跑到 bundle 最后一根 bar)。22/23 有效不影响 baseline 结论;彻底修需让回测在 bundle 末尾留 T+1 余量(单独 issue)。
2. **聚合 summary 的 logging TypeError**:`engine.py:272 _logger.info("  %s: %.4f", key, val)` 对 `timing`(dict)用 `%.4f` → `TypeError: must be real number, not dict`(被 logging 吞,不致命,report json 正常写出)。**阶段A就记过的次要 bug**,这次复现。本 PR 未顺手修(超范围);建议单独小修。

## 8. 改动清单(本 PR)

```
src/core/walk_forward/engine.py                       (+gap 逻辑 + _to_date + _load_trading_calendar)
tests/logic/test_walk_forward.py                      (折生成测试改 gap 断言 + run 测试注入 calendar)
tests/logic/test_walk_forward_dataset_cache_wiring.py (run 测试注入 calendar)
tests/logic/test_walk_forward_per_fold_timing.py      (run 测试注入 calendar)
tests/logic/test_walk_forward_embargo_gap.py          (新:7 项红线测试)
openspec/changes/fix-walk-forward-embargo-gap/        (提案)
```
`_segment_embargo.py` / 模型参数 / 荐股 / D3 / 配置 **未碰**。临时运行配置 `config_walk_c1_gpu.yaml` 不入 PR。
