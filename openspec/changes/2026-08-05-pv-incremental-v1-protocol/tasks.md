# Tasks: 2026-08-05-pv-incremental-v1-protocol

## 0. 提案签署
- [ ] 操作人签 PV-DP-1..8（本提案 PR merge = 签署，签字后冻结）

## 1. 并后执行序（各自独立 PR/点火，录以为序）
- [ ] 基线预测生成（csi800 Alpha158+LGB 逐折,IS+OOS,GPU 点火在
      操作人,provenance 三件套+ledger）
- [ ] 协议三件套常量替换（evaluator/FWER/adjudication →
      pv_incremental_v1）+ 冻结件（算子白名单/简约系数/正交带/
      FWER 最小 n/候选注册表）
- [ ] GP 搜索批次（点火在操作人）→ OOS 一次性评估 → FWER 裁决
      → 数字 STOP（阴性照报）
- [ ] 幸存者（若有）走 PV-DP-7 晋升门；否则干净阴性归档
