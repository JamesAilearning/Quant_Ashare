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
- [x] campaign miner 配置 config/factor_mining/pv_incremental_v1.yaml
      （七字段白名单 + close→close 目标 + 冻结适应度常量 + IS 窗）
      与终端白名单接线（面板/生成器/点变异三处同一字段集）
- [x] 测试：逻辑（hinge/engine/miner/exporter/whitelist/D5）+ 治理
      （含 campaign 配置逐字对齐冻结件）

## 2. 点火（并后，操作人）
- [x] 基线 walk-forward run（单次不间断，GPU 约 1-2 小时）  ← 台账 E004（28/28 折，commit a83276a，git_dirty=False）
- [x] 导出器 → 宽表 + sidecar → ledger E002 result 登记（数字原样）  ← 台账 E004（sha a254832c…；条目编号实际为 E004，非草案时预估的 E002）
- [x] GP 搜索批次（点火在操作人；先把导出器产出的 baseline 路径填入  ← 台账 E005（首批中止照录）+ E006（重跑后注册 top-50）
      config/factor_mining/pv_incremental_v1.yaml 的
      data.baseline_preds_path）→ 候选 manifest 注册
