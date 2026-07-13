# 阶段8 · quality_profitability_v1 · GATE REHEARSAL —— EXECUTED 6/6

> **目的:** 在任何决策级 run 之前,演练预注册闸门本身会不会拦(研究设计 §5:
> "演练至少应覆盖: 正常接受、未注册候选被 flag、dirty checkout 被拒、计划提交
> 晚于 run 被拒、数据 manifest 不一致被拒、PIT 案例失败被拒")。
> **性质:** 本文档 = 演练脚本 + 结果记录表。冻结时执行一遍,全 PASS 后填入
> 结果与 commit hash;任何 FAIL = 闸门本身有洞,先修门再谈冻结。
> **复用:** `docs/prereg/cadence_horizon.yaml` 先例的 git-provable gate 机制
> (plan-commit 早于 run + clean checkout + manifest 一致)。

## 演练矩阵(六场景,每场景一行结果)

| # | 场景 | 做法 | 期望 | 结果(2026-07-13 执行) |
|---|---|---|---|---|
| R1 | 正常接受 | clean checkout + 已冻结 plan + manifest 一致 + 合法候选 C1,干跑 gate 检查 | ACCEPT | **PASS**(GATE ACCEPT,回显 plan_commit+manifest) |
| R2 | 未注册候选被 flag | 伪造候选 id `C4_ROE` 请求评估 | REJECT: not in registered_candidates | **PASS**(REFUSE) |
| R3 | dirty checkout 被拒 | 工作树注入临时 untracked 文件再跑 | REJECT: dirty checkout | **PASS**(REFUSE) |
| R4 | 计划晚于 run 被拒 | 用早于 plan-commit 时间戳的 run 元数据 | REJECT: plan committed after run | **PASS**(REFUSE) |
| R5 | manifest 不一致被拒 | 最小篡改临时 store(每 endpoint 单文件)对照冻结 manifest(gate 无 override 可绕) | REJECT: content-hash mismatch | **PASS**(REFUSE) |
| R6 | PIT 案例失败被拒 | 注入前视探针(断言公告前可见,正确 view 使其失败) | REJECT: PIT case failed | **PASS**(REFUSE) |
| R7 | 未冻结 ledger 被拒(v3 增补) | 临时把 ledger status 降为 draft 再跑 | REJECT: ledger status not frozen | **PASS**(REFUSE) |
| R8 | 触碰 holdout 的窗口被拒(v4 增补) | --test-window-end 2025-12-31(默认 config_walk 末界)不带 --final-adjudication | REJECT: 点名 holdout;唯一次终裁须显式 flag | **PASS**(REFUSE) |
| R9 | 终裁越过 holdout 上界被拒(v5 增补) | --final-adjudication --test-window-end 2026-06-30 | REJECT: 终裁须恰好=holdout 末端(2026H1 不在注册裁决范围) | **PASS**(REFUSE) |
| R10 | 终裁只盖半个 holdout 被拒(v6 增补) | --final-adjudication + config(overall_end 2025-06-30) | REJECT: no partial peek —— 一次性终裁须覆盖完整已签 holdout | **PASS**(REFUSE) |
| R11 | 自报窗与 config 不符被拒(v7 增补) | --test-window-end 2024-12-31 + 真 config_walk.yaml(2025-12-31) | REJECT: claim/config mismatch —— gate 只信 config | **PASS**(REFUSE) |

## 各场景断言细则

### R1 正常接受
- 前置: `quality_profitability.yaml` + ledger + 本文件已 committed(冻结 commit);
  `git status --porcelain` 空;manifest 与 `D:/qlib_data/financial_pit_raw` 全量
  content-hash 一致。
- 断言: gate 输出 ACCEPT,并回显 plan commit hash + manifest hash 到 run 元数据。

### R2 未注册候选
- 断言: 以 `C4_ROE`(或任何不在 registered_candidates 的 id)请求 → 拒绝并要求
  先走"新计划 + 新未触碰窗"(prohibited_variants 条款);ledger 强制先记账。

### R3 dirty checkout
- 断言: 任何未提交改动(含 untracked 的 src/ 文件)→ 拒绝;错误信息给出
  `git status` 摘要。复用 cadence_horizon 的 clean-checkout 检查。

### R4 计划晚于 run
- 断言: run 元数据时间戳 < plan 最后一次 commit 时间戳 → 拒绝(git-provable:
  计划必须早于一切决策级 run)。

### R5 manifest 不一致
- 断言: store 任一文件 hash ≠ manifest 记录 → 拒绝并列出差异文件;演练后恢复
  原文件并复核 hash 归位。

### R6 PIT 案例失败
- 断言: 伪造"公告日前可见"行进入合成 store → view 层 PIT 案例断言失败 → 拒绝。
  (机制已有: tests/logic/test_financial_pit_view.py 的 availability 断言 +
  tests/governance 隔离门;演练 = 在 gate 流程里真跑一遍这组断言。)

## 执行记录

- 执行: Claude Code(操作人授权冻结序列,holdout(a) 已签)
- 执行时间: 2026-07-13 ~15:00 UTC
- 冻结 commit: 本冻结包所在 commit(自证;squash 合并后以 main 上本 PR 的合并
  commit 为准 —— gate 的 freeze 时间取全部 8 冻结件在所查 checkout 的最晚 commit,
  与任何临时分支 hash 无关)
- manifest aggregate: 4560e8536524e4a0…(1880 文件)
- 驱动: `scripts/research/rehearse_gate3_prereg_gate.py --store-dir D:/qlib_data/financial_pit_raw`
- 六场景结果: **6/6 PASS**(gate 无洞;R1 ACCEPT 回显 plan_commit,R2-R6 全部正确 REFUSE)
- gate 加固复跑(codex #352 r1: 移除 manifest override / freeze 覆盖全部 8 冻结件 /
  ACCEPT 回显 aggregate): 复跑 **6/6 PASS**(R1 回显
  manifest_aggregate_sha256=4560e853…;R4 消息升级为 frozen package 时间)。
- gate 加固 v2 复跑(codex #352 r2: verify 校验 aggregate 本体 / 冻结件须在
  checkout 真实存在)后复跑 **6/6 PASS**。
- **诚实记录(r4)**: r3 轮声称加入的 ledger 冻结状态强制**实际未落进 gate**
  (补丁静默失败,codex r4 抓出)。v3 补丁真实落地(带插入验证)并新增 R7 场景
  钉死该检查。教训: 声称的强制必须有演练场景盯着。
- **v4(codex r5)**: gate 新增 --test-window-end 强制校验冻结 dev 末界
  (2024-12-31),dev run 触碰 2025 holdout 即拒;唯一次终裁须显式
  --final-adjudication(响亮 UNBLINDING 横幅)。新增 R8 场景。
- **v5(codex r6)**: 终裁窗限定在已签 holdout 内(2026H1 带 flag 也拒);新增 R9。
- **v6(codex r7)**: 终裁窗必须恰好等于 holdout 末端 —— 中途窗=部分偷看,拒;R10。
- **v7(codex r8)**: 被门的窗口**从 run config 推导**(overall_end,extends 链
  解析),自报 --test-window-end 降级为交叉核对(不符即拒);ACCEPT 回显 config
  sha256 供 run provenance 绑定;R8 改用**真 config_walk.yaml** 演练默认配置
  攻击,新增 R11(claim/config mismatch)。演练矩阵 **11 场景**,最终复跑
  **11/11 PASS**(输出以 PR #352 评论存档)。
- 纪律: 每个决策级 run 前必先跑 `gate3_prereg_gate.py --candidate <id>`,ACCEPT 才可点火。
