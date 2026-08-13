# Tasks: 2026-08-07-risk-constraints-mode-optin

## 1. 实现（本 PR，#406）
- [x] 两引擎新增 `risk_constraints_mode`（缺省 `raise`）+ 校验
- [x] 两引擎新增 `metrics_purpose`（缺省 `official`，独立于 mode）+ 校验
- [x] 配置层交叉校验：宽松反应须同时声明预测用途
- [x] `BacktestRunner.run` 官方指标边界独立拒绝（缺省 `official`）
- [x] `PREDICTIONS_ONLY_METRIC_STATUS` + 宽松路径改贴
- [x] 状态传递：fold → 总表顶层 → run catalog（两引擎同构）
- [x] `metrics_purpose` 记在 strategy 层，不污染 `risk_constraints` 映射
- [x] 基线 preset 声明两键；约束限值一个未动
- [x] 更正 `risk_constraints.py` 中被本次跑证伪的注释
      （"the observed runs pass them"）
- [x] 治理测试 16 条（行为验证为主，源码文本钉标注局限）
- [x] 两引擎 schema parity 随之更新（`metric_status` 升入 SHARED）

## 2. 追认（codex #406 r6）
- [x] 补本 OpenSpec 变更（提案 + spec delta）
- [x] `openspec validate --strict`

## 3. 点火（并后，操作人）
- [x] 干净全量基线 walk-forward（新目录）  ← run pv_incremental_baseline_pre_rev1（19/19 折全成）
- [ ] 比对：16 个已成功 fold 的预测哈希须逐一不变，只多出 3 个
      **（归档时补做，结论与原文不符——照录）**：实测 prefix406(RAISE) vs
      pre_rev1(warn_and_clip) 的 19 折预测哈希，**13 折相同、6 折不同**
      （折 3,4,6,7,11,12）。机制已查明：判死折为 2/5/10，`ensemble_window=3`
      的 warm ensemble 使每个判死折污染其后 2 折——`{2,5,10}+{1,2}` 恰等于
      那 6 折。故原文写的不变量本身不成立，真实不变量是「非判死折下游的
      13 折不变」。**对本役无影响**：这两个 19 折 run 随后都被决策①-rev1 的
      28 折基线（E004，零失败折、无污染）取代，下游只消费 E004。
      留档意义：**一折失败会顺 warm ensemble 向后传播 ensemble_window-1 折**。
      （基准哈希已封存）
- [x] 基线导出 → E002 台账结果条目  ← 台账 E004（该 19 折版本随后被决策①-rev1 的 28 折版取代）
