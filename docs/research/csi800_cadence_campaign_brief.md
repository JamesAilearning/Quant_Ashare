# CSI800 降换手战役简报 — N5 三发 + DP-3 主判据 + veto 五项（2026-07-19）

**判定（预注册 DP-3 双条件，AND）：两条全过 = WIN。**
① conservative（20 bps）N5 全窗净超额年化 **+6.52% > 0**；
② 毛保持 **78.8% ≥ 50%**（N5 毛 +9.92% vs N1 毛 +12.59% × 50% =
+6.29% 线）。五条 veto 零触发，勾验状态 COMPLETE。
**本简报非晋升申请**：`promotion_eligible=false` 恒成立（attach 非
权威），晋升唯一权威是 merge 后的 certify verdict 侧车（三锚：pair +
N5 证据 + N1，`2026-07-17-csi800-cadence-campaign` DP-5）。

## 1. Run 事实记录

三发严格串行，全部跑在 main `e2a11e3`（= #378 R1 约束作用域修订合并
commit）。preset 为 #377 的三个 tracked 文件（2018-2025 全窗、24/3/3
步 3 → 23 折、ensemble 3、`campaign_v1` 约束、cadence 5/0/fold_phase、
`risk_constraint_scope: rebalance_days` 显式 opt-in）。

| run | preset | 折 | attestation |
|---|---|---|---|
| csi300 N5 参照 | `csi300_cadence5_reference.yaml`（5 bps, SH000300TR） | **23/23** | 23/23 折 `positions_sha256` + manifest 镜像 |
| csi800 N5 base | `csi800_cadence5_base.yaml`（5 bps, SH000906TR） | **23/23** | 同上 |
| csi800 N5 conservative | `csi800_cadence5_conservative.yaml`（20 bps, SH000906TR） | **23/23** | 同上 |

**首发点火事件（已档，R1 修订）**：R1 前的首发三发中，csi800 双档在
fold 4 被 `max_per_name` RAISE 击落（SH601127 @2021-05-12，5.04%/
5.05% vs 5.00% 上限——持有日市场漂移超幅，非配置决策）。定性：约束
逐日检查在 N=1（每日再平衡重置权重）与 N>1（漂移日也被查）下语义
不等价，同一数字 N>1 更严，破坏"同配置可比"。操作人**结果盲**签
选项 A → #378 以显式 opt-in 字段 `risk_constraint_scope:
"rebalance_days"` 将 campaign 约束校验对齐再平衡生效日（canonical
默认 `all_days` 全图合约逐字节不变；5% 数字、RAISE 模式、veto 数字
全部不动）。失败工件留证 `output/walk_forward/*_prer1_fullmap/`；
修订合并后三发全部重跑，结果盲保持至本简报判定时点。

## 2. 官方数字（逐折年化超额均值，净=with_cost）

| run | 毛超额 | 净超额 | mean IR | 净为正折数 | IC 1d |
|---|---|---|---|---|---|
| csi300 N5 参照（5bps） | +5.02% | **+3.43%** | 0.35 | 15/23 | 0.0246 |
| csi800 N5 base（5bps） | +9.93% | **+8.36%** | 0.74 | 16/23 | 0.0136 |
| csi800 N5 conservative（20bps） | +9.92% | **+6.52%** | 0.55 | 15/23 | 0.0136 |

N1 对照（#373 已证工件，哈希钉死的已提交源）：base 毛 +12.58% /
净 +6.08%；conservative 毛 +12.59% / 净 **−1.02%**。

- **战役论点被证实**：N1 conservative 的净 −1.02% 翻至 N5 的
  **+6.52%（+7.54pp）**。分解自洽：换手 24.5× → 6.49×（−73.5%）
  省成本 10.21pp（N1 隐含成本 13.61pp × 26.5% ≈ 3.61pp vs N5 实测
  3.40pp，吻合），毛塌缩仅 −2.67pp，净差 = 10.21 − 2.67 = 7.54pp
  与实测一致。
- **成本鲁棒性质变**：双档净差 (8.36−6.52)/15bps ≈ 0.123pp/bps，
  线性外推盈亏平衡滑点 ≈ **73 bps**——远在中盘实际成本区间
  （15-25 bps）之外；N1 时为 17-18 bps（区间内部，veto① 因此
  于首发战役触发）。
- 毛塌缩量级与 7b 一致性：csi300 参照毛 8.83% → 5.02%（−3.81pp，
  7b 实测 −3.88pp，机制重现）；csi800 毛 12.59% → 9.92%
  （−2.67pp）——原料基数大 4.6 倍，塌缩反而更小，毛保持 78.8%
  远在 50% 线上。
- 双档毛发散：N5 0.06% / N1 0.07%（≤5% fail-closed 线），同种子
  同预测敏感带构造成立（IC 双档完全一致 0.0136）。

## 3. veto 五项勾验（证据工件 `csi800_cadence_pair_report.json`，v3）

配对自证 + 参照三方认证先行通过：投影 diff 恰
`{slippage_bps: 5.0 → 20.0}` 一处；三 run 的 run_id + config/report/
fold_report sha256 全量入档；参照 23/23 折 official（
`ref_failed_folds=[]`）。

| # | 判据（跑前钉死） | 实测 | 触发? |
|---|---|---|---|
| ① | conservative 净超额 ≤ 0 | **+6.52%** | 否 |
| ② | csi500 sleeve 毛效应占比 > 80% | 44.1% | 否 |
| ③ | 年化单边换手 > csi300 **N5** 参照 × 1.5（同配置含 cadence） | 1.019×（6.49 vs 6.37） | 否 |
| ④ | 约束 provenance 缺失/漂移（46 折 `campaign_v1` 五键 + `risk_constraint_scope` config 声明 + 逐折披露） | 全录，零 problem | 否 |
| ⑤ | csi500 时均权重 > 75% 或 unknown 桶 > 10% | 48.7% / 0.0% | 否 |

veto③ 口径按 DP-4：参照 = csi300 N5（同配置含 cadence 三字段），
比率 1.02× 说明降频后 csi800 没有用额外换手买 alpha；base 档
1.020× 同性质。

## 4. DP-3 主判据（预注册双条件，certify 同口径预演）

| 条件 | 线 | 实测 | 结果 |
|---|---|---|---|
| (1) conservative N5 净超额年化 > 0 | 0 | +6.5193% | **PASS** |
| (2) N5 毛 ≥ N1 毛 × 50%（cons-to-cons） | +6.2932% | +9.9195%（保持 78.8%） | **PASS** |
| 前置：双臂毛发散 ≤ 5%（两对） | 0.05 | N5 0.0006 / N1 0.0007 | 过 |

数字来源：N5 侧 = pair v3 `per_fold_gross_annualized`（与锚上
fold report 重导一致性由 certify 复验）；N1 侧 = 哈希验证过的已提交
源 fold reports（`docs/research/evidence/csi800_n1_folds/`，46 折
sha256 == N1 pair v2 所钉）。本节是 certify 判定的**预演**，正式
判定以 merge 后 certify 产出的 verdict 侧车为准。

## 5. WIN 链后续（顺序不可倒置）

run → attach → **pair v3 + N5 源证据入库（本 PR）** → 用户 merge
（pair 锚 + 证据锚成立）→ certify（`git show origin/main` 全输入 +
attach 端到端重跑 + DP-3 重导 + 摘要链完备）→ verdict 侧车入库 PR →
用户 merge（侧车提交评审）→ 晋升成立。跳过任一环 = 晋升无效。
N5 证据目录 `docs/research/evidence/csi800_n5_runs/`（三 run 聚合 +
全部 fold reports + positions 本体，9.4 MB，`*.json -text` 字节
保真）。

## 6. 复盘要点

- 降频不是免费午餐但此处是净赚：省成本（10.2pp）÷ 毛塌缩（2.7pp）
  ≈ 3.8:1。7b 的教训（csi300 毛基数太薄，同比例塌缩即致命）反向
  验证了"原料升级→降频"的顺序纪律。
- R1 事件的元教训：跨节奏比较里，"同一约束数字"不等于"同一约束
  语义"。修订走了完整结果盲流程（零业绩数字暴露窗口内定性、签署、
  实现、五轮 codex、重跑），预注册纪律未破。
- csi300 N5 参照自身净超额 +3.43% > N1 参照的 +2.24%：降频对参照
  同样净改善，veto③ 的"同配置"口径（DP-4）因此是必要的——若参照
  仍用 N1，比率会无意义地趋零。
