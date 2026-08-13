# Tasks: 2026-08-06-pv-incremental-consumer-binding

## 1. 实现（本 PR）
- [x] 共享绑定模块（sidecar 必在 / 协议 / 摘要合法 / 字节恰等 / 基线身份）
- [x] 评估器接线（manifest 字节 + 基线身份）
- [x] 裁决器接线（manifest 字节）+ 判决记 registration_manifest_sha256
- [x] 修 #402 换行转换致摘要不符（newline=""）
- [x] 测试：13 条（含用真实注册器产出的端到端绑定与篡改检出）

## 2. 点火（并后，操作人）
- [x] 基线 walk-forward + 导出（台账 E004：28/28 折，IS 覆盖 1215/1215）
- [x] GP 批次 → 注册 → ledger 追加（E005 中止照录 / E006 注册 top-50）
- [x] OOS 一次性评估 → FWER 裁决 → 数字 STOP（E007：survivors 50/50，t≈5.11）
