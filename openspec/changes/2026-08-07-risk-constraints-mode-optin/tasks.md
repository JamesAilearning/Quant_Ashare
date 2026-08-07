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
- [ ] 干净全量基线 walk-forward（新目录）
- [ ] 比对：16 个已成功 fold 的预测哈希须逐一不变，只多出 3 个
      （基准哈希已封存）
- [ ] 基线导出 → E002 台账结果条目
