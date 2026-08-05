# Proposal: 阶段8 战役二协议 — pv_incremental_v1（csi800 价量增量因子）

## Why

Gate-4A（`quality_profitability_v1`）以全批 FWER = CLEAN_NEGATIVE
永久关闭了盈利质量原料方向（九 trial 观测 t 全负，max t −0.19；
2025 holdout 保持盲态，签字冻结不可反悔）。同期两条独立证据把
下一战场指向价量增量：CSI800 扩池 recon 毛超额 csi800 +3.68% >
csi300 +1.26% 且生产模型重仓中盘腿 61.8%；N5 晋升 ensemble 门
veto② 空转（干跑窗毛效应和 ≤ 0）再次实证 alpha 厚度是当前约束。
主计划判断不变：天花板 = alpha 单薄，真战场是因子。

本提案冻结阶段 8 第二场战役的完整协议。签字（merge）后协议不可
事后修改；数字产出后按三态规则裁决，干净阴性同样是有效产出
（花一次严格实验的成本关一扇门）。

## 战役目标（一句话）

在 csi800 上以 GP 搜索价量表达式，找出对生产基线 Alpha158+LGB
具**增量信息**的因子（判据 = 增量而非独立显著）：产出可送晋升链
的认证候选，或产出干净阴性关门。

## 操作人决策账（PV-DP 表——签字后冻结）

- **PV-DP-1 数据面**：universe = csi800（PIT membership spans +
  coverage stamp）；表达式输入 = 逐股 day bins **恰七字段**
  `open/high/low/close/volume/money/turnover_rate`（前复权口径）。
  估值/市值字段（pe/pb/ps/total_mv/circ_mv）**不入**表达式输入
  ——稀释价量假设且扩大多测面；不做 ex-financials 排除（价量族
  覆盖全 csi800，与 Gate-4A 的刻意差异，此处明示）。ST 掩码/
  embargo 沿既有管线算术。
- **PV-DP-2 窗口**：**IS**（GP 繁殖唯一可见窗）2018-01-01→
  2022-12-31；**OOS dev**（幸存者一次性评估）2023-01-01→
  2024-12-31；**2025 = holdout 盲态**（`holdout_unblinded` 单向
  标志，仅晋升候选终裁可揭，不可反悔）；**2026 段不触**（与生产
  运行重叠，留作观察期实证，挖掘全程禁用）。
- **PV-DP-3 目标函数（GP 适应度）**：IS 逐日截面 rank-IC 均值
  （1d forward，close-to-close lag-1，Gate-4A 同源语义）+ 简约压
  （节点数惩罚，系数冻结于实现 PR）+ **Alpha158 基线正交惩罚**：
  对基线预测的逐日截面 Spearman |ρ| 超过冻结带即罚——直接优化
  增量，不给"重新发明动量"留活路。基线预测 = csi800 Alpha158+LGB
  逐折 walk-forward 生成（IS+OOS 一次性，≈1-2 GPU 小时，点火在
  操作人），生成 run 的 provenance 三件套须全绿并入 ledger。
- **PV-DP-4 算子集**：沿用 factor_mining 既有冻结白名单（ts_rank/
  delay/delta/corr/cov/stddev/decay_linear/rank/sign/abs/log/
  min/max 族），表达式深度/长度上限沿 Phase 设计常数；实现 PR 把
  白名单与上限逐项列死，任何扩充 = 新提案。
- **PV-DP-5 FWER 机制**：family = 全部进入 OOS 评估的候选；块
  bootstrap q95 bar + **2.85 硬地板双门槛**（Gate-4A 同源）；
  **per-trial 最小 n 约束与稀疏 trial 处置写进冻结件**（Gate-4A
  复盘第 3 条：annual n=4 块重采抽重复位置致 null 重尾 bar 虚高
  +14.5——本役 pin：n < 冻结下限的 trial 不入 family、单独如实
  报告不参与裁决）；三态规则沿用，**clean-negative = reject_iff**。
- **PV-DP-6 切片纪律**（Gate-4A 复盘第 1/2 条）：任何切片注册时
  stamp 几何/参数一并写进冻结件（不留补签轮）；注册前必做零成本
  可行性 probe（mask 计数）且 probe 结果随注册入档——空转切片
  不占 N。
- **PV-DP-7 晋升门**：FWER 幸存者 → Phase-6 `MinedFactor` handler
  桥入 Alpha158+因子特征集 → 与基线 **walk-forward 配对比较**
  （run-comparison ruler 门 + runbook）→ 净口径（20 bps 保守单边）
  改善 + 操作人签字 → 进**独立的**生产晋升提案。生产接线不在本役
  scope；本役终点 = 认证候选或干净阴性归档。
- **PV-DP-8 工装绑定**：按 Gate-4A 归档 §5 纪律复用三件套骨架
  （evaluator/FWER/adjudication）：逐处替换协议常量为
  `pv_incremental_v1`、候选注册表/几何表重建、重走冻结+审签；
  adjudication SHALL 拒收 `protocol_id != pv_incremental_v1`
  的工件。D5 边界不变：`src/factor_mining/` 不直触 `qlib.*`/
  `src.pit.*`，数据一律经 `pit_adapter`。

## 铁律（全部沿袭）

绝不自动 merge / 数字原样呈报（阴性照报，verdict 归操作人）/
fail-loud 报操作人裁决 / 2025 holdout 不揭盲 / 预注册跑后不可改 /
勿删 FAIL 工件 / run 前后 ledger 成对登记 / 干净树点火。

## What Changes（本提案 PR 本体）

仅协议冻结文书（本文件 + tasks + spec delta）。实现（评估器常量
替换/GP 接线/基线预测生成/冻结件）在签字后的后续 PR，逐件走
codex 轮。

## 执行序（签字后）

1. 基线预测生成（GPU 点火在操作人，≈1-2 小时，provenance 入 ledger）
2. 协议三件套常量替换 + 冻结件 PR（1-2 轮）
3. GP 搜索批次（点火在操作人）→ OOS 一次性评估 → FWER 裁决
   → 数字 STOP（阴性照报）
4. 幸存者（若有）走 PV-DP-7 晋升门

## Non-goals

- 不碰生产服务与 N5 观察期；
- 不碰基本面水平值方向（Gate-4A 已关门）；
- 不在本役内做生产接线。
