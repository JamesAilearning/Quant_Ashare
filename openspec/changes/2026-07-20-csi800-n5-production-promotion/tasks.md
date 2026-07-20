# Tasks: 2026-07-20-csi800-n5-production-promotion

## 0. 提案签署（本 PR）
- [ ] 操作人签 DP-1..DP-6（proposal.md 决策账），签字后冻结

## 1. PR-A — 服务节奏机制（唯一 runtime 触点）
- [ ] iso-week 再平衡日判定（交易日历驱动：ISO 周第一个交易日；
      跨年周/长假周/单日周确定性测试）
- [ ] daily_recommend 输出工件增 `rebalance_day` 字段 + 非再平衡日
      HOLD 提示（schema 追加字段；如与 artifact schema v2 requirement
      原文冲突则该 PR 内 MODIFIED 全文重述）
- [ ] csi800 服务参数落地（universe/benchmark/cadence 语义与胜者
      preset 同值；服务侧白名单差异字段写死）
- [ ] 治理测试：服务参数 vs 胜者 preset 恰差白名单 pin
- [ ] codex review 循环 + CI 绿 → STOP 等 merge

## 2. PR-B — 候选训练 + guard eval（结果盲）
- [ ] 训练配置定稿（Alpha158+LGB、csi800、train 2018-01-02..最新
      可用-embargo、topk 50/n_drop 5/label horizon 同 campaign 族）
- [ ] **训练点火 = 用户执行（GPU，阶段6 先例）**；产物 =
      候选 pkl + meta（含 fit_start/fit_end_for_inference）
- [ ] guard 窗日期跑前钉死（最近干净窗口，写死入 PR 文本）
- [ ] frozen guard eval（eval_frozen_model_oos 同族口径升级
      csi800/SH000906TR/N5/20bps）跑毕——数字保持未读直至 PR-C
      数字 STOP
- [ ] codex review 循环 + CI 绿 → STOP 等 merge

## 3. PR-C — 晋升执行（数字 STOP）
- [ ] 晋升工具执行前置校验：侧车 --verify 通过 +
      promotion_eligible: true（否则拒绝，零写入）
- [ ] guard eval 硬 veto 勾验（0 degenerate/0 straddle/净>0/五 veto
      数字沿 canonical spec）
- [ ] pre-promote 备份（pkl+meta 带时间戳）+ docs/promotion/ 新
      baseline json + 现任基线保留
- [ ] canonical pkl + meta 替换；runbook 修订为周节奏操作卡
      （含观察期纪律与 73bps 盈亏平衡参考）
- [ ] **数字 STOP**：guard eval 全部数字首次呈报 → codex/CI →
      用户 merge = 晋升执行完成
- [ ] 若任一门不过：如实入档不晋升，现任不动，处置另行提案

## 4. 收束
- [ ] 观察期起点记录（首季度只记录不回调）
- [ ] 战役记忆/runbook 终稿同步 → `/opsx:archive`
