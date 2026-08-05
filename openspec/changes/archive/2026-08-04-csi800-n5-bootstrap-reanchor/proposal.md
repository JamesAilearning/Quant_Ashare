# Proposal: CSI800 N5 自举三元组重锚定 — v1 门拒后按冻结公式重注册

## Why

首次自举（v1 三元组，`2026-07-20-csi800-n5-production-promotion`
PR-C' #392 预注册，T=2026-02-13）于 2026-08-03 点火并全链走到
成员级门，结果**如实拒绝**：

| v1 成员 | 训窗 | valid 窗 | trainer 门 | valid 窗 IC(1d) | 判定 |
|---|---|---|---|---|---|
| m1 | 2023-08-14..2025-08-13 | 2025-08-18..2025-11-18 | PASS (30/1000) | +0.025870 | PASS |
| m2 | 2023-11-13..2025-11-13 | 2025-11-18..2026-02-13 | PASS (104/1000) | **−0.016346** | **FAIL** |
| m3 | 2024-02-19..2026-02-13 | 2026-02-26..2026-05-26 | PASS (210/1000) | +0.030255 | PASS |

按 runbook 与 spec（"自举任一门失败时不切换"scenario）：**自举
中止**，零生产写入，现任 canonical 续任，三门工件（含 FAIL 件）
随本提案入档 `docs/research/evidence/csi800_n5_runs/
bootstrap_v1_gates/`。spec 对该失败动作的显式出路是"处置（重训
失败成员或另行提案）升级为操作人决策点"——操作人已裁决走
**另行提案 = 本提案**。

点火前的两项数据侧实事（同日完成，均在 repo 树外）：
1. index_weight 原始 dumps 止于 2025-12-31（2026-06 调样从未拉
   取），先全量重拉（至 2026-08-03）+ 03 重解析 sleeve 快照；
2. v1 预注册窗口尾=旧 bundle 尾 2026-06-17，qlib 回测需 T+1 日历
   日，**钉在尾上的窗口在旧 bundle 上物理不可回测**（认证 isoweek
   run 末折 test 止于 2025-12-31，从未踩过此边）。经
   `daily_update.py` 全链（fetch→registry→bins→membership→
   universe→benchmark→validate→原子 swap）bundle 尾延至
   **2026-08-03**。

## 重锚定理据（为何是公式重推而非挑窗）

R1-DP-C 的三元组从来是**相对切换日**的几何：T-6m/T-3m/T 错峰、
24 月滚动训窗 + 3 月 valid，T 落后 bundle 尾约 4 个月（给 valid +
test 留可观测空间）。v1 的 T=2026-02-13 对旧尾 2026-06-17 正是
该几何；实际切换日推迟至八月、bundle 尾已至 2026-08-03，按**同一
冻结公式**在新尾重推即得本提案三元组（尾差 +47 天的平移 + 交易日
吸附）。三点诚实声明：

1. **门原样不动**：成员级/ensemble 级门判据、阈值、工装（#390-
   #392）零改动；
2. **新中间成员照样可能 FAIL**：m2' 新 valid 窗（2026-01-06..
   2026-04-03）与 v1 m2 的 FAIL 窗（2025-11-18..2026-02-13）重叠
   约 5.4 周，公式重锚定不保证过门——过不了就再次如实中止；
3. **三名成员全部重训**：v1 的 m1/m3 run（含其 PASS 门工件）与
   2026-07-21 旧候选同等如实弃置，不混装新旧窗口成员。

## 重锚定决策账（RA-DP 表——签字后冻结）

- **RA-DP-1 新三元组窗口（bundle 日历实算，尾 2026-08-03）**：

  | 成员 | 训窗 | 跨度 | valid 窗 | test 窗（日频诊断，非晋升证据） |
  |---|---|---|---|---|
  | m1' | 2023-09-28..2025-09-29 | 732d | 2025-10-10..2026-01-09 (91d) | 2026-01-14..2026-02-27 |
  | m2' | 2023-12-29..2025-12-30 | 732d | 2026-01-06..2026-04-03 (87d) | 2026-04-09..2026-05-21 |
  | m3' | 2024-04-01..2026-04-01 | 730d | 2026-04-07..2026-07-07 (91d) | 2026-07-10..2026-07-31 |

  推导规则与 v1 同源：fit_end 错峰 92/92d（serving pin [75,100]
  内）；train_start = fit_end−730 自然日就近交易日吸附（跨度 pin
  [700,745] 内）；valid_start = fit_end 后第 3 个交易日；
  valid_end = valid_start+3 个日历月向前交易日吸附；test_start =
  valid_end 后第 3 个交易日；test 长度承袭 v1 各成员（45/42d），
  但 m3' 终点 = **bundle 尾前最后一个交易日**（qlib 回测需 T+1
  结算日——v1 把 m3 test 与干跑窗钉在尾上正是其物理不可回测的
  根因，本次修正该几何而非承袭它；codex #393 r1）。
- **RA-DP-2 ensemble 干跑窗** = **2026-05-06..2026-07-31**
  （trailing quarter 86d；操作人签署值 05-04 为休市日顺延至首个
  交易日 05-06，签署终点 08-03 为 bundle 尾、按同一 T+1 规则收至
  尾前一交易日 07-31（codex #393 r1）；起点在 T'=2026-04-01 后
  35 天，对三成员训窗全样本外）。
- **RA-DP-3 v1 证据入档**：三门工件 JSON（m1 PASS/m2 FAIL/m3
  PASS）+ 拒绝简报（`docs/research/csi800_n5_bootstrap_v1_gate_
  refusal.md`）随本提案入库；FAIL 工件永不删除。
- **RA-DP-4 预注册纪律不变**：本提案 merge = 新窗冻结生效；随后
  三发 GPU 串行点火 → 成员门×3 → 候选 manifest → ensemble 门 →
  数字 STOP → 切换执行器（#392 工装原样消费新 preset 与新
  `BOOTSTRAP_DRYRUN_WINDOW`）。

## What Changes

1. `config/presets/csi800_n5_bootstrap_m{1,2,3}.yaml`：仅六个窗口
   日期键按 RA-DP-1 更新（头注释同步）；其余逐字不动（"三 preset
   仅窗口键可差异"治理钉守继续生效）。
2. `scripts/bootstrap_ensemble_cutover.py`：`BOOTSTRAP_DRYRUN_
   WINDOW` → RA-DP-2 值。
3. 钉守随动：`tests/governance/test_csi800_n5_bootstrap.py` 窗口
   pin 表、`tests/logic/test_bootstrap_cutover_lib.py` 夹具、
   `tests/logic/test_bootstrap_cutover_executor.py` `_WINDOWS`、
   `docs/csi800-n5-production-runbook.md` 操作卡窗口表。
4. RA-DP-3 证据入档（新文件，只增不改）。
5. spec delta：自举中止后的重注册路径显式成文（ADDED
   requirement）。

## Non-goals

- 不改任何门判据/阈值/工装代码（除 RA-DP-2 常数）；
- 不动 v1 已训 run 目录与 `output/`（机器本地，如实弃置）；
- 不触碰季度轮换维护路径与年检义务。
