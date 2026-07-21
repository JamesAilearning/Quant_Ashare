# CSI800 N5 生产晋升 guard eval 简报 — 硬门不过线，如实入档（2026-07-21）

**判定：DP-3 gate 3 失败——候选冻结模型 guard 窗净超额年化
−2.14% ≤ 0，晋升中止，现任 canonical 不动**（`2026-07-20-
csi800-n5-production-promotion` DP-3"任一不过如实入档"条款）。
处置另行提案（见 §5）。

## 1. Run 事实

- **候选训练**（PR-B 义务，操作人授权点火）：pipeline run
  `20260721_195924_801348_64b5a26b_d995d43ff26f`，干净树 main
  `ac79c6b`（#387 merge），csi800 / SH000906TR / campaign 三守卫，
  ④ 镜像窗（train 2018-01-02..2024-12-18 / valid 2025-01-02..
  2025-06-26 / test=guard 2025-07-01..2026-06-12），GPU LGB
  best_iteration 555，`pkl_sha256 045f2d03…fe30361f`，
  metric_status official。模型本体留在 run 目录（不入库，④ 惯例）。
- **guard eval**：`eval_frozen_model_oos --profile csi800_n5`
  （knob 全预注册：csi800/SH000906TR/20bps/N5 iso_week/
  rebalance_days/campaign_v1），显式 fit 窗 2018-01-02..2024-12-18，
  231 个预测日、49 个可执行 stamp。工件
  `docs/research/evidence/csi800_n5_guard_eval/
  csi800_n5_candidate_guard.json`。结果盲保持至本简报呈报时点。

## 2. 晋升门逐项

| 门 | 判据 | 实测 | 结果 |
|---|---|---|---|
| A 战役资格 | 侧车 `--verify` + `promotion_eligible` | verified OK（6a2a6409…），eligible=true | PASS |
| B iso_week 复核 | 锚上证据全窗净 > 0 | **+6.01%**（毛 +9.59%，23/23 折，与 fold_phase 胜者 +6.52%/+9.92% 同量级——锚切片稳健） | PASS |
| C-1 退化 | 0 degenerate / 0 straddle（可执行 stamp 集） | 0 / 0（min_unique 751/800，全 stamp 集同样干净） | PASS |
| C-2 约束 | campaign_v1 RAISE 零触发 | `constraint_veto: null`；max_single_name_weight 4.39% < 5% | PASS |
| C-3 集中度 | 行为参照 | median 50 持仓 / top10 25.6% / HHI 0.0207（分散） | PASS |
| **C-4 净超额** | **guard 窗净年化 > 0** | **−2.14%**（毛 +0.90%，IC1d +0.0252，IC 正比率 61.2%） | **FAIL** |

## 3. 诊断（为什么战役 WIN 而 guard FAIL）

成本不是主因：毛 +0.90% − 净 −2.14% = 成本拖累 ~3.0pp，与战役
N5 成本算术（~3.4pp）一致。**主因是毛 alpha 在 guard 年塌缩**：
候选冻结模型 guard 窗毛 +0.90% vs 战役 walk-forward 毛均值 +9.92%。
两个结构性差异叠加：

1. **协议 vs 冻结**：战役证据是**协议级**的——每折季度新训
   （鲜度 ≤1 季）+ ensemble 3；guard eval 是单一冻结模型（训至
   2024-12-18，guard 年内鲜度衰减 6-18 个月）。④ 的复盘早已证明
   鲜度是本方法的一等因子（incumbent 陈旧一年毛从正转 −3.08%）。
2. **guard 年本身是协议级弱年**：锚上 isoweek 复核 run 的晚期折
   （季度新训）在 guard 年区间：fold 21 净 **−27.9%**、fold 22 净
   +3.0%（fold_phase 胜者同期 −32.0%/−1.1%）——即使季度新训的
   协议在这一年也约为平-负。战役 +6.5% 是 2018-2025 全窗均值
   （含 fold 19 +68.9% 这类肥年），近期 regime 明显偏瘦。

结论：**gate C-4 的"冻结单年净>0"设计与被认证的"滚动重训全窗
均值"证据存在结构性错配**——弱年 + 冻结鲜度惩罚双重叠加下，
该门几乎必然拒绝任何诚实候选。门如实执行了它的字面职责；错的
不是执行，是门的形状。这正是预注册纪律要暴露的信息。

## 4. 判定与现状

- 晋升中止，**现任 canonical（csi300 时代 alpha158_lgb_pit.pkl）
  不动**；候选 pkl 留 run 目录待处置。
- 战役 WIN 侧车（#383）不受影响——它认证的是协议级策略语义，
  本次失败的是"用冻结模型近似协议"的晋升门。
- PR-A 落地的服务节奏机器（HOLD reader/两级绑定链）与 PR-B 的
  isoweek 复核证据全部有效在库，为后续处置复用。

## 5. 处置选项（另行提案，操作人裁决）

1. **（推荐）协议对齐提案**：生产实现被认证的协议本体——季度
   重训节奏（每季新训候选，轻量 per-retrain 门：退化/约束/集中度/
   IC 方向，同 C-1..C-3）+ N5 iso_week 服务；性能依据 = 已认证的
   全窗战役证据 + DP-6 观察期，如实弃用"单年冻结净>0"门（其
   结构性错配已被本次实证）。需新 OpenSpec change 修订 DP-3。
2. **等数据重试**：bundle 更新后以更鲜候选重跑同门——鲜度惩罚
   减小但"单年窗 vs 全窗均值"的错配仍在，弱年照样拒。
3. **收束不上生产**：现任继续（其 guard 窗净 −3.08%，并不比
   candidate 好），战役成果封存至 regime 或方法改善。

## 6. 预期管理（无论选项几）

近两折（≈guard 年）协议级净 ≈ −28%/+3%——**被认证的 +6.5% 是
跨 8 年均值，不是对任何单一年份的承诺**。若走选项 1，观察期的
诚实预期是：季度间波动巨大（单季年化 ±30-70%），edge 只在均值
意义上存在。
