# Proposal: CSI800 N5 conservative 上生产 — 认证胜者的晋升实操

## Why

降换手战役（`2026-07-17-csi800-cadence-campaign`，已归档）以完整
机器可验链判定 **WIN**：verdict 侧车（#383，`csi800_cadence_verdict_v1`，
三锚 `3ffceb4`，`--verify` 复验 OK）记录 conservative（20 bps）N5
全窗净超额年化 **+6.5193% > 0**、毛保持 **78.81% ≥ 50%**、五 veto
零触发、`producer_digest_certified`、`promotion_eligible: true`。

当前生产（④ 2026-06-30 晋升的 canonical `alpha158_lgb_pit.pkl`）
在 guard 窗净超额 **−3.08%**，成本拖累 ~5.8%/yr——④ 简报当时已
明示"下一个战略杠杆是降频/降成本，alpha 存在，日频再平衡吃掉它"。
本战役正是那个杠杆的确证形态：换手 24.5×→6.49×（−73.5%），盈亏
平衡滑点 17-18 bps → **≈73 bps**（脱离中盘实际成本区间 15-25 bps）。
晋升实操 = 把认证过的策略语义（csi800 × N5 × campaign_v1 约束 ×
rebalance_days 作用域）接进生产服务路径。

## 操作人决策账（DP 表——签字后冻结）

- **DP-1 晋升对象 = 策略语义 + 新生产候选模型（两者分离评审）**：
  1. **服务语义**：universe `csi800`、benchmark `SH000906TR`、
     N=5 再平衡节奏、`campaign_v1` 约束 + `rebalance_days` 作用域
     语义、20 bps 保守成本口径（运维预期管理基准）。
  2. **生产模型**：**新训 csi800 候选**（Alpha158 + LGB，训窗
     2018-01-02 → 最新可用日减 label embargo，csi800 universe，
     与 campaign fold 配置同族：topk 50 / n_drop 5 / label horizon
     同款），经 DP-3 晋升门后替换 canonical pkl + meta（④ 先例：
     pre-promote 备份件回滚）。
  - 明确 **NOT**：不直接拿 campaign fold 模型上生产（fold 训窗止于
    历史折点，鲜度不足）；不允许现任 csi300 时代模型给 csi800 打分
    （分布外，战役证据不覆盖该组合）。
- **DP-2 生产节奏锚 = iso-week（每 ISO 周第一个交易日为再平衡日），
  锚的证据义务前置（codex #385 r1）**：campaign 的 `fold_phase` 锚
  在生产无对应物；7b 已预承诺 iso_week 为胜者复核切片。但认证胜者
  preset 的锚是 `fold_phase`——`v2-rebalance-cadence` 明确两者是
  不同 schedule，生产绑定 SHALL NOT 建立在未经复核的锚漂移上。
  故 **PR-B SHALL 先跑 iso_week 复核 run**（7b 预承诺的胜者复核
  切片的落地形态）：新增 preset
  `csi800_cadence5_conservative_isoweek.yaml`，与认证胜者 preset
  恰差 **{rebalance_anchor, output_dir}**（治理测试钉死该恰差），
  单发串行；**复核判据（跑前钉死）**：iso_week 切片全窗净超额
  年化 > 0，且与 fold_phase 胜者的毛/净差如实入档（诊断披露，
  不设第二道数值门——锚切片是稳健性复核不是重新选型）。复核过
  线后生产锚才成立；不过线 = 如实入档、晋升中止、锚问题另行
  提案。非再平衡日 `daily_recommend` 照常可跑（监控用途），但
  输出 SHALL 携带 `rebalance_day: false` 并醒目 HOLD 提示；周中
  ST/退市/停牌事件**不触发中途调仓**（与回测 N5 语义一致——
  持有日只有市场漂移，卖出在下一再平衡日处理，如实入档该口径
  差异由 DP-6 观察期覆盖）。
- **DP-3 晋升门（新候选模型，数字预注册，跑后不可改）**：
  1. certify 侧车机器前置：晋升工具 SHALL 验证已提交侧车
     `--verify` 通过且 `promotion_eligible: true`，否则拒绝执行
     任何 pkl 替换（战役 WIN 是本次晋升的资格来源）；
  2. ④ 式 frozen guard eval（`eval_frozen_model_oos.py` 同族口径，
     csi800/SH000906TR/N5 语义）硬 veto：0 degenerate days、
     0 cutoff-straddle days；
  3. guard 窗（最近干净窗口，跑前钉死具体日期）N5 语义、20 bps
     净超额年化 **> 0** vs SH000906TR——不过线如实入档不晋升
     （④ 的"freshness 例外"不适用：campaign 已证净转正可达，
     生产门槛就是净转正）；
  4. 五 veto 数字沿 `v2-csi800-expansion-guards` canonical spec
     原样适用于 guard eval 产物；
  5. **iso_week 复核门（DP-2，锚定工件——codex #385 r3）**：复核
     证据 SHALL 已并主线，门经 `origin/main` 锚 `git show` 读取
     （与战役 certify 同口径），验证 config 绑定已提交 preset +
     净超额年化 > 0 从锚上 report 重导；本地未锚定输出拒绝。
  **零写入范围界定（codex #385 r2）**：前置或任一门失败时
  SHALL NOT 触碰 canonical 生产工件（pkl/meta/备份/基线）——但
  失败记录本身 SHALL 写入（guard eval 产物 + 入档文本是失败路径
  的义务产出，审计不可缺）；"拒绝执行且不产生任何写入"仅指
  晋升执行本体（canonical 替换及其附属写入）。
- **DP-4 回滚与基线（④ 先例）**：替换前 SHALL 落
  `alpha158_lgb_pit_pre_promote_<ts>.pkl` 备份 + meta 备份；
  `docs/promotion/` 新增 csi800 N5 口径 baseline json，现任
  基线保留为回滚记录；回滚 = 恢复备份件一步完成。
- **DP-5 治理 pin（两级绑定链，codex #385 r1）**：
  1. iso_week 复核 preset vs 认证胜者 preset 恰差
     **{rebalance_anchor, output_dir}**（治理测试钉死）；
  2. 生产服务参数 vs **iso_week 复核 preset** 的语义字段恰差
     白名单（仅限服务侧必要字段，跑前写死）。
  两级相接：serving → iso_week 复核 preset → 认证胜者 preset，
  每级差异显式钉死，锚漂移不再有白名单逃逸。
  `docs/daily-recommend-runbook.md` 修订为周节奏操作卡；晋升
  执行步骤全部入 runbook。
- **DP-6 预期管理与观察期**：+6.52% 是 walk-forward 20 bps 回测
  口径，**非实盘承诺**；上线后首个季度为观察期——只记录不回调
  任何预注册数字；holdout 未揭盲不可反悔纪律沿用；若实盘实测
  成本显著超 20 bps（向 73 bps 盈亏平衡余量侵蚀过半），观察期
  报告如实呈报，处置另行提案。

## What changes

- **MODIFIED capability `v2-daily-stock-recommendation`**（本提案
  delta 为 ADDED requirements，既有 requirement 原文不动）：
  1. 生产服务节奏（DP-2：iso-week 再平衡日判定 + `rebalance_day`
     披露 + 非再平衡日 HOLD 语义）；
  2. 生产晋升门（DP-3：certify 侧车前置 + guard eval 硬 veto +
     DP-4 回滚件义务）；
  3. 生产 preset 与 campaign 胜者绑定（DP-5 治理 diff pin +
     20 bps 口径入运维文档）。
- NO 官方回测语义变更：runtime 的 cadence/scope 机制已全部在位
  （7a + #378 R1 + #380/#381/#382），本 change 只动服务层与晋升
  流程层。

## Impact

- 分阶段 PR（本提案先行签署，每 PR 独立 STOP，用户唯一合并点）：
  1. **PR-A 服务节奏机制 + 两级绑定链工件**：daily_recommend
     iso-week 再平衡日判定 + 输出标记 + csi800 服务参数 +
     **iso_week 复核 preset（纯配置先入库）+ 两级恰差治理测试**
     （codex #385 r2：比较对象先于测试存在）——唯一 runtime 触点；
  2. **PR-B iso_week 复核 run + 候选训练 + guard eval**：复核 run
     （消费 PR-A preset）→ 训练点火（GPU，用户执行——阶段6
     先例）→ frozen guard eval（结果盲至数字 STOP）；
  3. **PR-C 晋升执行**：pkl + meta 替换 + 备份件 + baseline json +
     runbook 修订 + 治理 pin → **数字 STOP 签字**（guard eval 全部
     数字首次呈报于此）→ 用户 merge = 晋升执行完成。
- 若 DP-3 任一门不过：如实入档不晋升，现任 canonical 不动，
  处置另行提案。

## 风险如实入档

- 回测→实盘口径差：walk-forward 无实盘冲击成本/流动性约束；
  73 bps 盈亏平衡提供 ~3.6× 余量但不是保证。
- csi800 中盘真实成本可能高于 20 bps 预注册值（probe 实证区间
  15-25 bps 的上沿）；DP-6 观察期覆盖。
- 新训候选与 campaign fold 模型非同一工件——campaign 证明的是
  **协议级** alpha（滚动重训 + N5），生产以同族配置的最新模型
  近似该协议；此近似的残差由 DP-3 guard eval 把门。
