# v2-factor-mining-foundations — delta for 2026-08-05-pv-incremental-v1-protocol

## ADDED Requirements

### Requirement: pv_incremental_v1 战役 SHALL 以增量判据与冻结协议运行

`pv_incremental_v1` 战役（csi800 价量增量因子）SHALL 满足全部下列协议义务，任何偏离 SHALL 以新提案重新签署而非事后修改：

1. 表达式输入 SHALL 恰为七个价量字段（open/high/low/close/
   volume/money/turnover_rate），估值/市值字段不得进入表达式；
2. GP 适应度 SHALL 含对 Alpha158 基线预测的正交惩罚——候选以
   **增量信息**为判据，独立显著不构成通过；基线预测生成 run 的
   provenance SHALL 全绿并入 ledger；
3. 窗口语义 SHALL 为 IS 2018-2022（GP 唯一可见）/ OOS dev
   2023-2024（一次性评估）/ 2025 holdout 盲态（单向揭盲仅限晋升
   终裁）/ 2026 段禁用；
4. FWER SHALL 用块 bootstrap q95 + 2.85 硬地板双门槛，且冻结
   per-trial 最小 n——n 低于下限的 trial 不入 family、单独如实
   报告；三态规则 clean-negative = reject_iff；
5. 切片 SHALL 在注册时冻结 stamp 几何/参数，注册前 SHALL 完成
   零成本可行性 probe 且结果随注册入档；
6. 三件套工装 SHALL 绑定 `protocol_id = pv_incremental_v1` 并
   拒收异协议工件；D5 边界（factor_mining 不直触 qlib/pit）
   SHALL 保持。

#### Scenario: 独立显著但无增量的候选不通过

- **WHEN** 候选在 OOS 独立 rank-IC 显著但对基线正交性检验显示
  信息高度重合（超冻结带）
- **THEN** 该候选不构成 FWER 幸存者语义下的晋升入场券——增量
  判据是通过的必要条件

#### Scenario: 2025 holdout 在无幸存者时保持盲态

- **WHEN** OOS 评估 + FWER 裁决产出干净阴性（无幸存者）
- **THEN** 2025 holdout 不揭盲，`holdout_unblinded=false` 状态
  保持，战役以有效排除归档

#### Scenario: 稀疏 trial 不污染 FWER family

- **WHEN** 某注册 trial 的有效样本 n 低于冻结下限
- **THEN** 该 trial 不进入 bootstrap family，其数字单独如实报告
  且不参与裁决——稀疏重采导致的 null 重尾不得抬高全族门槛
