# Proposal: 风险约束违规反应可配 + 非 canonical 指标状态

追认性提案（codex #406 r6）。本变更起初被当作"暴露一个配置开关"实现，
过程中长成了 canonical 运行时的行为变更 —— 我未在跨过门槛时重新评估。
决策与规格在此补齐并接受审阅。

## Why

pv_incremental_v1 战役的基线首跑（csi800，19 folds，2018-04..2024-12）
有 3 折被判死。根因不是数据、也不是选股越界，而是回测层 `max_per_name`
被**持仓漂移**击穿：

```
fold 02  SH600132   6.20%~6.55%  (limit 5.00%)  6 天   2020-12
fold 05  （6 条违规）
fold 10  SZ001203 5.03%~5.08% / SZ000932 5.01%~5.17%  9 天  2022-11~12
```

fold 10 那几条超了 0.01~0.17 个百分点：建仓合规，标的上涨后权重被动越线。

这一跑的产物是**样本外预测**。战役唯一的增量判据（正交惩罚）只吃
`baseline_preds.parquet`；组合构建与收益率与判据无关。而预测在回测**之前**
产生、不受 clip 影响 —— 直接证据是 `fold_02_predictions.pkl` 在回测抛异常
的情况下仍写出了 46,812 行完好预测（零 NaN，覆盖整个季度）。

于是一条 0.01pp 的 post-trade 越线废掉了整整一个季度的基线覆盖，而那些
空洞随后会被口径①（`penalize_covered_days_only`）**静默吸收** —— 正是本役
一路在防的静默降级。

同时必须防住反向风险。核实 `backtest_runner.py` 的原注释后确认：clip 是
**post-trade**，`return_series` / `risk_analysis` 来自 qlib 的**未 clip**
执行，`positions` 也刻意与之绑定，只有旁支 `positions_clipped` 是 clip 过
的。所以容忍违规**并不软化数字** —— 它发出的正是 RAISE 存在的意义所要拒绝
的那批收益。若它们仍贴 canonical 标签，这个开关就成了绕过 RAISE 校验的通道。

## What Changes

- `PipelineConfig` / `WalkForwardConfig` 新增 `risk_constraints_mode`
  （`raise` 缺省 = 现行语义，既有配置零变化）与 `metrics_purpose`
  （`official` 缺省）。两者**互相独立**：从 mode 派生 purpose 等于把守卫
  的钥匙交给被守卫的开关。
- `BacktestRunner.run` 在官方指标边界拒绝"容忍违规但未声明用途"的调用。
  配置层同样拒绝，两道独立。
- 新增 `PREDICTIONS_ONLY_METRIC_STATUS`；宽松路径改贴此值，并沿
  fold → 总表 → run catalog 一路传递（两引擎同构）。
- `run_catalog.build_record` schema 增 `metric_status` / `metrics_purpose`。
- 基线 preset 显式声明两键。约束**限值一个未动**。
- `metrics_purpose` 记在 `strategy` 层，**不进** `risk_constraints` 映射
  （战役 veto 对该映射做精确等值比较）。

## Impact

- 既有配置行为不变（缺省即现行语义）。
- 新字段进 config fingerprint —— 必须，否则 resume 会把 RAISE 与 CLIP 的
  fold 混进同一个 run。既有可 resume 的跑一次性失效。
- 报告与 catalog 新增两个键（两引擎同构，parity 测试已随之更新）。

## Non-goals

- 不放宽任何约束限值。
- 不改 clip 的 post-trade 语义。
- 不让宽松跑产出可用于官方比较的收益率 —— 相反，本变更的一半工作就是
  确保它们在每一层都不被读成官方数字。
