# Tasks

- [x] 事实核对：canonical 滑点=单标量（无分段框架；"688/300 ±20% …"系
      limit_threshold 口径，per-instrument 本身是 audit A4 backlog）。
- [x] DP-1/2/3/4 操作人签署（2026-07-16，全按推荐）。
- [x] 本提案 + spec delta（veto 数字表随 spec 冻结）。
- [ ] guard-1+3 实现 PR：`config/presets/csi800_conservative.yaml`
      （slippage_bps=20，其余与 csi800.yaml 逐字段一致；过 per-universe
      配对治理测试）+ veto 表治理测试（存在性 + 五条数字 pin，防跑后
      篡改）。
- [ ] guard-2 实现 PR：attribution 层 sleeve 接线——pipeline +
      walk-forward 显式 config 键，与 industry taxonomy 配置互斥校验，
      SleeveResolutionError fail-loud 透传，per-sleeve 换手
      （walk-forward holdings 序列）。
- [ ] 三件全绿 → 战役点火工单（walk-forward 全窗 + 敏感带成对 +
      sleeve 报告 + veto 勾验），单独 STOP 等操作人。
- [ ] backlog 一行：per-instrument 分段滑点成本模型（audit A4 同族）。
- [ ] Archive after merge（`/opsx:archive`）。
