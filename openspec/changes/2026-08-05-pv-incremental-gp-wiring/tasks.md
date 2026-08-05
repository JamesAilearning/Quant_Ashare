# Tasks: 2026-08-05-pv-incremental-gp-wiring

## 1. 实现（本 PR）
- [x] 冻结件补三口径（决策①②③）+ baseline.overall_end + governance pin
- [x] FitnessConfig 惰性字段 + banded hinge（v1 公式/pin 不变）
- [x] GPEngine baseline 穿线 + 日截面 Spearman + 未覆盖计数
- [x] baseline_key 缓存/checkpoint 失效纪律
- [x] miner 基线装载 + provenance 绑定 + run 记录覆盖披露
- [x] 基线导出器（折 sha/窗口/ensemble/重复/provenance/不覆盖）
- [x] preset（overall_end 2024-12-31 圣规防线）
- [x] ledger E001 intent 预登记
- [x] 测试：24 逻辑（hinge/engine/miner/exporter/D5）+ 2 治理

## 2. 点火（并后，操作人）
- [ ] 基线 walk-forward run（单次不间断，GPU 约 1-2 小时）
- [ ] 导出器 → 宽表 + sidecar → ledger E002 result 登记（数字原样）
- [ ] GP 搜索批次（点火在操作人）→ 候选 manifest 注册
