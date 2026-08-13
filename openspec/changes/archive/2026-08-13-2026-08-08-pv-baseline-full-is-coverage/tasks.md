# Tasks: 2026-08-08-pv-baseline-full-is-coverage

## 0. 签署（操作人，本 PR 合并即视为签署）
- [x] 决策①-rev1：批准将基线 overall_start 退至 2015-10-01  ← 操作人签署 = PR #412 合并
      （train 窗保持 24m；OOS 折分毫不动；理由与影响见 proposal）

## 1. 实现（本 PR）
- [x] preset 增 overall_start: "2015-10-01"（含数据边界注文）
- [x] 治理钉：折几何推导首个 test 窗 == 冻结 IS 起点（负向验证：
      退回 2018-01-01 即红）
- [x] 引擎守卫：train_start 早于 bundle 日历首日即 fail-loud
      （负向验证：旧 bundle + 2015 起点即拒）
- [x] 导出器：check_run_config_binding 增绑 overall_start
- [x] 冻结件 is_coverage_policy 注文追加 rev1 备注（原文不删，
      标注"2026-08-08 经 2026-08-08-pv-baseline-full-is-coverage
      修订，见台账 E00x"）

## 2. 台账（签署后、点火前）
- [x] 追加决策①-rev1 条目：引用本 change、新旧 overall_start、  ← 台账 E002（含程序偏差披露与裁决 B）
      折数 19→28、新 bundle content_hash（append-only，E001 不改写）

## 3. 点火序（并后，操作人裁决节点不变）
- [x] QUANT_PROVIDER_URI=my_cn_data_pit_2015 重跑基线（28 折）  ← 台账 E004（28/28 折）
- [x] 验收：IS 覆盖 670 → ~1215 天；OOS 8 折 test 窗与旧配置逐一相同  ← 归档时实测：IS 670→1215 天；OOS 8 折 test 窗与旧配置逐一相同（已核）
- [x] 重新导出 + E002 结果条目  ← 台账 E004（导出 sha a254832c…；条目编号为 E004）
