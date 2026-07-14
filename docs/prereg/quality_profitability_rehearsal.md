# 阶段8 · quality_profitability_v1 · GATE REHEARSAL —— EXECUTED 20/20

> **目的:** 在任何决策级 run 之前,演练预注册闸门本身会不会拦(研究设计 §5:
> "演练至少应覆盖: 正常接受、未注册候选被 flag、dirty checkout 被拒、计划提交
> 晚于 run 被拒、数据 manifest 不一致被拒、PIT 案例失败被拒")。
> **性质:** 本文档 = 演练脚本 + 结果记录表。冻结时执行一遍,全 PASS 后填入
> 结果与 commit hash;任何 FAIL = 闸门本身有洞,先修门再谈冻结。
> **复用:** `docs/prereg/cadence_horizon.yaml` 先例的 git-provable gate 机制
> (plan-commit 早于 run + clean checkout + manifest 一致)。

## 演练矩阵(二十场景,每场景一行结果;R1-R6=研究设计 §5 最低要求,R7-R20=codex 对抗加固增补)

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
| R12 | 库外 run config 被拒(v8 增补) | 临时目录 config(overall_end 2024-12-31,窗口本身合法)传入 --run-config | REJECT: NOT under the repository —— 边界必须 git-provable(tracked) | **PASS**(REFUSE) |
| R13 | extends 链出冻结包被拒(v9 增补) | 临时给自包含 dev preset 追加 extends: config_walk.yaml(未冻结父)再跑 | REJECT: NOT part of the frozen package —— 全解析链必须冻结,防参数漂移 | **PASS**(REFUSE) |
| R14 | 重复终裁被拒(v9 增补) | 临时把 ledger holdout_unblinded 翻 true,再跑 --final-adjudication(窗口精确合法) | REJECT: ALREADY UNBLINDED —— 一次裁决被消费后永拒 | **PASS**(REFUSE) |
| R15 | 链值层 env 占位符被拒(v10 增补) | 临时把 dev parent 的 provider_uri 换回 ${QUANT_PROVIDER_URI:-…} 再跑 | REJECT: env placeholder —— 同一 sha256 不得在运行期解析到不同数据 bundle | **PASS**(REFUSE) |
| R16 | 候选/config 绑定不符被拒(v10 增补) | --candidate C2_PROF(已注册)配 C1 绑定 stub | REJECT: binding mismatch —— gate 只认 config 实际评估的候选 | **PASS**(REFUSE) |
| R17 | 无绑定 config 被拒(v10 增补) | 直接用 parent 快照(无 gate3_candidate,窗口/链全合法)跑 | REJECT: declares no gate3_candidate —— 绑定是必须项 | **PASS**(REFUSE) |
| R18 | 终裁 flag 配 dev 窗被拒(v12 增补) | --final-adjudication + dev stub(2024-12-31) | REJECT: DEV window —— 终裁 flag 只对精确 holdout 窗有效,dev run 不得携带终裁 provenance | **PASS**(REFUSE) |
| R19 | 持有期漂移被拒(v13 增补) | 临时把 dev parent 的 cadence 63 翻回 1(日频默认) | REJECT: holding-period mismatch —— 日频换手/成本指标不得冒充已签季度设计 | **PASS**(REFUSE) |
| R20 | 宇宙章戳漂移被拒(v13 增补) | 临时把 gate3_universe 换成 csi300_full | REJECT: universe stamp mismatch —— 全 csi300 不得顶冻结 ex-金融宇宙之名 | **PASS**(REFUSE) |

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
  commit 为准 —— gate 的 freeze 时间取全部冻结件(v10 起 16 件,含两个
  run-config parent 快照与 6 个候选绑定 stub)在所查 checkout 的最晚 commit,
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
  攻击,新增 R11(claim/config mismatch)。演练矩阵扩至 11 场景,复跑 11/11 PASS。
- **v8(codex r9)**: ① run config 必须**位于 repo 内且 git-tracked**(库外
  /tmp config 不受 clean-tree/冻结检查覆盖,边界不可证)—— 决策级窗口由此全部
  锚定在两个随包冻结的 preset(`config/presets/quality_gate3_dev.yaml` 末界
  2024-12-31 / `quality_gate3_final_adjudication.yaml` 末界 2025-12-31,冻结件
  从 8 件扩为 **10 件**);② canonical PIT battery 不可被调用方替换 ——
  `--pit-cases`(替换语义)改为 `--extra-pit-case`(仅追加,canonical
  `tests/logic/test_financial_pit_view.py` 永远强制)。R1/R9/R10 演练改走
  tracked preset(R9/R10 以 bytes I/O 临时改写终裁 preset 的 overall_end 再
  还原),新增 R12(库外 config 拒收)。演练矩阵扩至 12 场景,复跑 12/12 PASS。
- **v9(codex r10)**: ① 两个 preset **物化为自包含快照**(不再 extends
  config_walk.yaml —— 未冻结父文件的后续 commit 会在 preset hash 不变的情况下
  漂移 模型/宇宙/成本/折 参数);gate 新增全链检查:run config 解析出的
  **extends 链上每个文件都必须是冻结件**,否则拒;ACCEPT 逐链回显 sha256。
  ② 一次性终裁机器化:ledger 新增 `holdout_unblinded` 布尔字段(冻结时
  false;终裁点火后立即翻 true+追加条目+commit),gate 见 true 即**永拒**任何
  后续 --final-adjudication(缺失/非布尔=账本畸形,亦拒);UNBLINDING 横幅
  写明点火后的强制账本动作。新增 R13(extends 出包拒)/R14(重复终裁拒)。
  演练矩阵扩至 14 场景,复跑 14/14 PASS。
- **v10(codex r11)**: ① 数据路径冻结字面化:两个 parent 快照的三处
  ${QUANT_*:-…} 全部解析为 canonical 字面路径(env 间接会让同一 config
  sha256 在运行期解析到不同 qlib/ST/退市 bundle,数据版本锁失效);gate 对
  链上任何**值层** ${...} 占位符即拒(注释提及豁免,与 loader 只展开字符串值
  的语义一致)。CLAUDE.md 的 env-var 规则针对可移植 preset;冻结 prereg
  config 有意用确定性换可移植性(preset 头注明)。② 候选绑定机器化:新增
  6 个 3 行候选 stub(extends 冻结 parent + gate3_candidate,冻结件 10→16),
  gate 从 config 链 child-first 派生 gate3_candidate,缺失或与 --candidate
  不符即拒 —— 裸 CLI 声明不再能"gate C1 跑别的"。runner
  (run_walk_forward.py)未知键 allowlist 加惰性键 gate3_candidate(引擎今日
  不消费;Gate-4B 增列接线落地时必须消费并核对)。注册检查前移至绑定检查前
  (R2 拒因语义不变)。新增 R15(值层占位符拒)/R16(绑定不符拒)/R17(无绑定
  config 拒)。演练矩阵扩至 17 场景,复跑 17/17 PASS。
- **v11(codex r12)**: v10 的 runner 侧"惰性 allowlist"升级为 **fail-loud
  拒跑** —— allowlist 只是不报错,runner 仍会丢弃该键、把 C1/C2/C3 stub 全部
  跑成同一个裸 Alpha158 parent,GATE ACCEPT 就会给"无增列的裸跑"背书。现
  run_walk_forward.py 见 `gate3_candidate` 即 ValueError(仿 stamp_tax_bps
  前例,先于未知键检查),Gate-4B 接线落地时必须以真实消费替换该守卫。守卫由
  CI 测试钉死(tests/logic/test_run_walk_forward_mined.py::
  test_load_config_gate3_candidate_fails_loud_until_4b_wiring —— 换守卫必须
  连测试一起换);runner 侧强制走 CI 测试而非 gate 演练场景,矩阵维持 17。
- **v12(codex r13, P2)**: --final-adjudication 误配 dev config 时终裁分支
  整体被跳过 —— ACCEPT 会携带 [FINAL ADJUDICATION] provenance 却既不覆盖
  holdout 也不消费 unblinding 状态(dev run 冒充终裁)。现 flag 存在即强制
  派生窗==holdout 精确窗,dev 窗直接拒。新增 R18,矩阵扩至 18。
- **v13(codex r14)**: preset 语义对齐预注册设计。① 持有期章戳:parent 快照
  写入 rebalance_cadence_days=63 / rebalance_phase=0 / anchor=fold_phase
  (决策③季度;63=21x3,每折相位重置 → 3 个月 test 折恰好一次再平衡 =
  2025 holdout 全年 4 次,精确对齐 plan 注) —— 不写则引擎默认日频,指标会
  冒充已签设计;gate 交叉核对三元组与 plan holding.primary 映射(非
  quarterly_rebalance 即拒,不猜)。② 宇宙章戳:parent 写入声明性
  gate3_universe=csi300_pit_ex_financials;runtime 今日无 ex-financial 排除
  机制,实施排除是 Gate-4B 接线硬义务 —— runner 守卫泛化为一切 gate3_* 键
  fail-loud(CI 测试同步覆盖 gate3_universe),gate 核对章戳==plan
  study_design.universe。新增 R19(cadence 漂移拒)/R20(宇宙章戳漂移拒)。
  演练矩阵 **20 场景**,最终复跑 **20/20 PASS**(输出以 PR #352 评论存档)。
- 纪律: 每个决策级 run 前必先跑 `gate3_prereg_gate.py --candidate <id>`,ACCEPT 才可点火。
