# Delta: v2-daily-stock-recommendation — CSI800 N5 生产晋升

## MODIFIED Requirements

### Requirement: Daily recommendation SHALL emit a ranked, dated, persisted buy list

The path SHALL rank tradable candidates by predicted score descending,
truncate to the configured `topk` (default 50), and emit a list whose
rows carry `as_of_date, entry_date, rank, stock_code, stock_name,
predicted_score, tradable_flag, unavailable_reason`. Ranks SHALL be
contiguous `1..N` with `N ≤ topk`. The list SHALL be persisted as both
`daily_recommendation_<date>.csv` and `.json`, and printed to the
terminal. The two time points — `as_of_date` (data cutoff T) and
`entry_date` (suggested entry T+1) — SHALL both appear.

**Cadence-aware entry semantics（本次修订，codex #385 r3/r4——
消除 HOLD 日双重语义且不触碰 as-of 契约）**：在节奏化生产配置
（cadence ≠ 1）下，工件 SHALL 携带 `rebalance_day: true|false`
与 `next_rebalance_date`（下一再平衡日，追加字段）；
`rebalance_day: true` 时本列表是可执行的 T+1 入场清单（上文语义
原样）；`rebalance_day: false` 时工件仍按同 schema 持久化（行
内容、排名、双时间点含 `entry_date` = 下一交易日**全部照常**——
`entry_date` 的 as-of 解析契约不因节奏而变）但 SHALL NOT 构成
入场指令——输出 SHALL 附醒目 HOLD 提示，入场参考由
`next_rebalance_date` 字段承载而非改写 `entry_date` 语义。日频
配置（cadence = 1，含现行为）下每日皆再平衡日，语义与本
requirement 原文逐字一致、路径不变。

#### Scenario: output is ranked and bounded
- **WHEN** `recommend` produces a result with `topk = 50`
- **THEN** the buy list has at most 50 rows
- **AND** rows are ordered by `predicted_score` descending with
  contiguous ranks `1..N`
- **AND** a `daily_recommendation_<date>.csv` and `.json` are written
  carrying both `as_of_date` and `entry_date`

#### Scenario: HOLD 日工件不构成入场指令且 entry_date 契约不变
- **WHEN** 节奏化生产配置下在非再平衡日（例：周一再平衡后的
  周二）运行 recommend
- **THEN** 工件照常持久化，`entry_date` 仍等于下一交易日（as-of
  解析契约原样），`rebalance_day: false` + HOLD 提示标明列表为
  监控视图而非入场指令，`next_rebalance_date` 携带下一再平衡日

## ADDED Requirements

### Requirement: 生产服务 SHALL 以 iso-week 节奏披露再平衡日语义

生产服务（daily_recommend）SHALL 按 **每 ISO 周第一个交易日 =
再平衡日** 的锚判定当日角色，并在输出工件中携带
`rebalance_day: true|false` 与 `next_rebalance_date` 字段（HOLD
日的工件语义由上文 MODIFIED 的 buy-list requirement 唯一定义，
本 requirement 只负责锚判定与字段披露）。周中 ST/退市/停牌事件 SHALL NOT 触发中途
调仓——卖出在下一再平衡日处理，与认证回测的 N5 语义（持有日仅
市场漂移、约束仅在再平衡生效日校验）保持一致。再平衡日判定
SHALL 由交易日历驱动（节假日顺延至该 ISO 周内第一个实际交易日；
整周无交易日则该周无再平衡日），判定逻辑 SHALL 有确定性测试
覆盖（跨年 ISO 周边界、春节长假周、单日交易周）。

#### Scenario: 节假日周锚顺延

- **WHEN** ISO 周第一个日历工作日为节假日
- **THEN** 该周再平衡日 = 该 ISO 周内第一个实际交易日

### Requirement: 生产模型晋升 SHALL 以 certify 侧车与 guard eval 双门把守

晋升路径的执行 SHALL 满足下列全部前置，缺一即拒绝执行。本
requirement 管辖**晋升路径**：首次生产切换（自举 ensemble
上线）与任何策略级变更（universe/cadence/约束/成本口径改动）；
**季度成员轮换是独立的维护路径**（见"生产打分 SHALL 实现认证
协议本体"requirement——codex #389 r1：轮换不改策略语义，SHALL
NOT 重跑侧车/iso_week 门，其前置在彼处定义）。**零写入的范围
限于晋升执行本体**（canonical pkl/meta 替换、备份件、基线
记录），失败路径的审计记录（guard eval 产物、如实入档文本）
SHALL 照常写入，二者不冲突（失败必须留痕，canonical 必须
不动）：

1. **战役资格门**：已提交 verdict 侧车经
   `csi800_campaign_certify.py --verify` 复验通过且
   `promotion_eligible: true`（晋升资格唯一权威，沿
   `v2-csi800-expansion-guards`）；
2. **iso_week 复核门（锚定工件，codex #385 r3）**：复核 run 的
   证据 SHALL 已提交至钉死证据路径并从 `origin/main` 可达锚经
   `git show` 读取（与战役 certify 同口径）——门 SHALL 从锚上
   字节验证：(a) 复核 run 内嵌 config 绑定已提交的 iso_week
   复核 preset（config 哈希比对）；(b) 全窗净超额年化 > 0 由
   锚上 report 重导，非操作人断言。本地/未锚定的复核输出
   SHALL 被拒绝——生产锚（iso-week）与认证胜者锚（fold_phase）
   是不同 schedule，未经锚定复核的锚漂移 SHALL NOT 进入生产
   绑定；
3. **per-retrain 轻门（R1 修订，替代已废止的"冻结单年净>0"门
   ——该门与协议级认证证据结构性错配，实证见
   `docs/research/csi800_n5_promotion_guard_brief.md`）**：每名
   新成员进入生产 ensemble 前 SHALL 全过：(a) trainer 完整性
   （best_iteration/valid loss 有限，且 best_iteration SHALL NOT
   等于 num_boost_round——早停从未触发即训练预算耗尽的边界
   异常，codex #389 r12）。**num_boost_round 机读源（codex #389
   r18——现行 sidecar 不含该字段）**：trainer sidecar schema
   SHALL 扩展携带 `num_boost_round`（训练实际使用值，随 sidecar
   写盘），门 SHALL 仅从 sidecar 读取两值比较；sidecar 缺
   `num_boost_round` 字段 = 完整性门失败（fail-closed，SHALL NOT
   回退到 preset 默认值或跳过边界检查）；(b) 退化门
   （新 ensemble 对 trailing quarter 可执行 stamp 0 degenerate /
   0 straddle）；(c) 约束干跑（trailing quarter N5 回测
   campaign_v1 RAISE 零触发）；(d) IC 方向门（valid 窗
   IC(1d) > 0）；(e) serving veto 面：干跑 attribution 上
   veto②/⑤ 数字原样（<80% / <75% / <10%），veto③ = 干跑年化
   换手 ≤ 锚上 iso_week 复核 run 换手均值 ×1.5。**净收益
   SHALL NOT 作为 per-retrain 门**——业绩权威 = 已认证战役证据 +
   年度再认证。**本晋升路径（自举/策略级变更）下的失败动作**
   （codex #389 r14——与轮换维护路径的"沿用旧 ensemble/两季
   升级"动作严格分离，后者仅存在于协议本体 requirement）：任一
   门不过 = 切换中止、现任 canonical 及其服务语义不变、失败
   如实入档、升级为操作人决策点，SHALL NOT 出现"沿用旧
   ensemble"分支（自举时旧 ensemble 不存在）；
4. **回滚件义务**：替换前 SHALL 写 pre-promote 备份（pkl + meta，
   带时间戳）并在 `docs/promotion/` 落新基线记录，现任基线保留；
   回滚 SHALL 为恢复备份件的单步操作。

per-retrain 门的全部数字 SHALL 于对应执行 PR 的数字 STOP 首次
呈报，跑后 SHALL NOT 修改判据或数字。

#### Scenario: 侧车缺失或复验失败时拒绝晋升

- **WHEN** 晋升工具在无已提交侧车、或 `--verify` 失败、或
  `promotion_eligible != true` 的状态下被调用
- **THEN** 拒绝执行，canonical 工件（pkl/meta/备份/基线）零
  写入，失败原因记录写入审计档，报错指向缺失的前置

#### Scenario: 晋升路径下任一轻门不过即中止切换

- **WHEN** 自举（或策略级变更）中任一轻门失败
- **THEN** 切换中止、现任 canonical 及其服务语义不变、门工件与
  结果如实入档、升级为操作人决策点（轮换维护路径的"沿用旧
  ensemble/两季升级"动作不适用于本路径）

#### Scenario: 训练预算耗尽的成员触发晋升路径中止

- **WHEN** 晋升路径（自举/策略级变更）中某成员的 trainer
  sidecar 记录 `best_iteration == num_boost_round`（早停从未
  触发）
- **THEN** trainer 完整性门失败（边界异常非收敛信号）→ 切换
  中止、现任 canonical 及其服务语义不变、失败如实入档、升级为
  操作人决策点（"成员不入 ensemble/沿用现行"是季度轮换维护
  路径专属动作，不适用于本路径；部分 ensemble 继续 SHALL NOT
  发生）

#### Scenario: 未锚定的 iso_week 复核输出被拒绝

- **WHEN** 晋升工具被指向一个仅存在于本地工作树（未提交/未
  合并主线）的 iso_week 复核 report
- **THEN** iso_week 复核门拒绝通过，报错指向锚定义务，canonical
  零写入

### Requirement: 生产打分 SHALL 实现认证协议本体（季度重训 + ensemble 3）

生产打分 SHALL 由**最近三名季度成员模型的 ensemble** 产生（与
walk-forward `apply_ensemble` 同语义——认证战役证据的预测生成
方式），SHALL NOT 以单一冻结模型近似协议（结构性错配已实证，
见 `docs/research/csi800_n5_promotion_guard_brief.md`）。每季度
末 SHALL 训练一名新成员（同族配置：Alpha158/LGB/csi800/campaign
三守卫，24 个月滚动训窗 + 3 个月 valid，embargo 同 walk-forward
折算术），经 per-retrain 轻门后轮换进 ensemble（最老成员退出）。
serving SHALL 经 manifest 消费三成员（pkl + meta 逐一列出，
视为一个逻辑模型；manifest 缺员/断链 SHALL fail-loud 拒绝出单）。
**季度轮换是维护路径而非晋升路径（codex #389 r1）**：其前置
SHALL 为且仅为 (a) 现行战役认证有效（已提交 verdict 侧车在库且
年度再认证未过期、未 LOSE）；(b) 新成员通过 per-retrain 轻门；
(c) 轮换前 SHALL 写 pre-rotation manifest 备份（单步回退到上一
ensemble）。**轮换路径的轻门失败动作**（codex #389 r14——与
晋升路径的"中止切换"动作严格分离）：新成员任一轻门不过 =
该成员不入 ensemble、现行 ensemble 沿用、门工件如实入档；
连续两季不过 SHALL 升级为操作人决策点。轮换 SHALL NOT 重跑
侧车 `--verify`/iso_week 门——
它们锚定的是策略语义，成员轮换不改变策略；年度再认证过期或
LOSE 期间轮换路径 SHALL 冻结（升级操作人决策点）。
**有效期锚（codex #389 r2/r5，确定性机读）**：认证有效期 =
**15 个月**（12 个月再认证周期 + 3 个月执行宽限），锚 = **状态
工件路径**在 `origin/main` 的 tip commit committer 日期（
`git log -1 --format=%cI origin/main -- docs/promotion/
csi800_recert_status.json`），SHALL NOT 锚在侧车路径（其非年检
触碰会漂移有效期——codex #389 r5）、SHALL NOT 依赖操作人断言
或本地文件时间戳。
**认证状态单一单调工件（codex #389 r3/r4）**：certify 在 LOSE
时按设计拒写侧车，且跨路径 committer 日期比较对乱序合并不
鲁棒——故认证状态 SHALL 由**单一状态工件**
`docs/promotion/csi800_recert_status.json` 唯一承载：每次年度
再认证（含首次自举）SHALL 将其更新为最新状态并走 PR 入库，
内容 SHALL 含 `verdict: WIN|LOSE`、对应 verdict 侧车的内容
哈希引用（WIN 时）、证据锚 commit 与判定说明。轮换执行器
SHALL 仅经 `git show origin/main:<状态工件路径>` 读取该文件——
**状态由文件内容直接给出，SHALL NOT 以跨路径日期/拓扑推断**；
`verdict: LOSE` 即冻结，新 WIN 状态合并即恢复。15 个月有效期
以状态工件路径在主线的 tip commit committer 日期起算（月级
粗粒度视界，日级合并乱序无实质影响；状态正确性本身不依赖
日期）。状态工件 SHALL 仅由年检流程与首次自举修改（治理
测试钉守——侧车路径的非年检触碰不影响轮换判定）。
**年度再认证义务**：每年 SHALL 以最新数据重跑战役协议全链
（walk-forward + pair/attach/certify）并**更新状态工件**
（codex #389 r6：产物是状态工件的新状态，非无条件的新侧车）——
WIN 时 certify 产新 verdict 侧车且状态工件携其内容哈希引用；
LOSE 时 certify 按设计不写侧车，状态工件单独承载 LOSE 判定。
再认证 LOSE = 生产降级决策点（操作人裁决），季度轻门 SHALL NOT
承担净业绩职责。
**首次自举门语义（codex #389 r13——自举时无旧 ensemble 可回退，
门的对象与失败动作须显式定义）**：首次上线 SHALL 以三名错峰
成员自举（训窗终点 T-6m/T-3m/T）；**成员级门**（trainer 完整性、
valid 窗 IC > 0）SHALL 对三名成员逐一评估；**ensemble 级门**
（退化、约束干跑、serving veto 面②③⑤）SHALL 对组装后的三成员
ensemble 整体跑一次（trailing quarter 干跑）。任一成员级或
ensemble 级门失败 = **自举中止**：不执行切换、现任 canonical
续任、失败如实入档，处置（重训失败成员或另行提案）升级为
操作人决策点——自举无"沿用旧 ensemble"分支，那是季度轮换
维护路径的失败动作。

#### Scenario: 自举任一门失败时不切换

- **WHEN** 三成员自举中任一成员级门（trainer/IC）或 ensemble
  级门（退化/约束干跑/veto 面）失败
- **THEN** 切换不执行、现任 canonical 与其服务语义不变、失败
  门工件如实入档、升级为操作人决策点

#### Scenario: ensemble manifest 缺员时拒绝出单

- **WHEN** serving manifest 声明的三成员中任一 pkl/meta 缺失或
  哈希断链
- **THEN** daily_recommend fail-loud 拒绝出单，报错指向缺失
  成员，绝不静默降级为部分 ensemble 或单模型

#### Scenario: manifest 重复成员身份时拒绝出单（codex #390 r4）

- **WHEN** serving manifest 的三成员槽位间任一身份字段重复
  （`pkl_path`/`pkl_sha256`/`meta_path`/`meta_sha256`——含同一
  pickle 内容以不同路径拼写出现）
- **THEN** manifest 加载 fail-loud 拒绝（重复成员会把三成员
  ensemble 静默退化为均值意义上的单/部分模型），拒绝发生在
  触碰任何模型字节之前

#### Scenario: 成员框架版本漂移时拒绝出单（codex #390 r3）

- **WHEN** serving 加载某成员时，其 trainer sidecar 记录的训练
  框架版本（按 sidecar `model_type` 对应的框架）与 serving 环境
  已安装版本不一致、该框架不可导入、或 sidecar 无法解析/缺
  `model_type`/缺版本字段/其 `pkl_sha256` 与 manifest 矛盾
- **THEN** daily_recommend fail-loud 拒绝出单（框架 minor 升级
  可静默改变 booster 序列化语义——walk-forward ensemble 的
  sidecar 版本守卫同语义，但 serving 拒绝而非跳过）

#### Scenario: ensemble 工件身份字段语义（codex #390 r3）

- **WHEN** ensemble 模式产出推荐工件
- **THEN** 工件 meta **不携带** `model_pkl_sha256`（该字段语义
  保留给单模型 pickle 摘要，决策页以其交叉核对 trainer sidecar
  的 `pkl_sha256`——挪用会使合法 ensemble 工件被误报为"其他
  模型"）；ensemble 身份由 `meta.ensemble.manifest_sha256` 承载，
  `model_path` 指向 manifest，成员三元组逐一列出

#### Scenario: 年度再认证 LOSE 触发降级决策点

- **WHEN** 年度再认证 walk-forward 全链产出 LOSE 判定
- **THEN** 结果如实入档并升级为操作人决策点（回滚/停用），
  生产 ensemble 在裁决前不自动变更

#### Scenario: 轮换 SHALL 绑定 gate 工件的被测窗口（codex #391 r19）

- **WHEN** 轮换执行器消费一份 `overall: PASS` 且摘要绑定正确的
  gate 工件，但其 `window` 块缺失、非日期、跨度越界、结束日远早于
  轮换时刻（陈旧）或落在未来；或成员级工件的 valid 窗未严格晚于
  该成员训窗终点（非样本外）/ 起点距训窗终点过远（并非该成员的
  valid 窗）
- **THEN** 轮换拒绝执行（manifest 零写入）——摘要绑定只证明"门测
  了哪些工件"，被测窗口绑定才能排除"在更容易的时期或陈旧时期测
  出的 PASS"；ensemble 级 trailing quarter 允许与训窗重叠（其职责
  是行为面而非业绩，R1 无净收益门），仅受时效与跨度约束

#### Scenario: 自举成员窗口豁免时效界而 ensemble 干跑窗不豁免（PR-C'）

- **WHEN** 首次自举的切换执行器消费三名成员的 gate 工件——其 valid
  窗按 R1-DP-C 的错峰设计（训窗终点 T-6m/T-3m/T）**刻意落在过去**
- **THEN** 成员级工件豁免"被测窗口须临近当前时刻"的时效界（其余
  绑定——样本外、紧邻训窗、跨度、不落未来、摘要与 fit 窗绑定——
  一律照旧）；**ensemble 级 trailing quarter 干跑窗不豁免**（它必须
  描述当下）。豁免仅存在于自举路径，季度轮换维护路径无此豁免

#### Scenario: 再认证过期期间季度轮换被冻结

- **WHEN** 年度再认证已过期（或 LOSE 未裁决）时尝试季度成员轮换
- **THEN** 轮换路径拒绝执行（manifest 零写入），升级为操作人
  决策点——维护路径的合法性以现行认证有效为前提

### Requirement: 生产服务参数 SHALL 经两级治理绑定链锚定认证胜者

生产服务参数 SHALL 经**两级恰差链**锚定认证胜者，且 SHALL NOT
经白名单吸收锚漂移——生产锚（iso-week）与认证胜者锚
（`fold_phase`）在 `v2-rebalance-cadence` 下是不同 schedule：

1. **iso_week 复核 preset**（`csi800_cadence5_conservative_isoweek`
   ，7b 预承诺的胜者复核切片落地形态）与认证胜者 preset
   `csi800_cadence5_conservative.yaml` 恰差
   **{rebalance_anchor, output_dir}**，治理测试钉死；其复核 run
   净超额年化 > 0 是晋升门的一部分（见晋升门 requirement）；
2. **生产服务侧参数**与 iso_week 复核 preset 的语义字段恰差
   SHALL 仅限服务侧必要字段（白名单跑前写死入治理测试），
   universe / benchmark / cadence 数值语义 / 约束校准 / 作用域 /
   成本口径 SHALL 同值。

20 bps 保守成本口径与 73 bps 盈亏平衡参考 SHALL 记入运维
runbook 作为预期管理基准；观察期纪律（首季度只记录不回调）
SHALL 同步入档。

#### Scenario: 服务参数漂移被治理测试拦截

- **WHEN** 有人修改生产服务参数使其与 iso_week 复核 preset 的
  语义字段产生白名单之外的差异
- **THEN** 治理测试失败，指出漂移字段与所需的 OpenSpec 变更路径

#### Scenario: 锚漂移不得经白名单逃逸

- **WHEN** 有人试图把 rebalance_anchor 加入服务侧白名单以绕过
  iso_week 复核
- **THEN** 治理测试失败——锚差异仅存在于第一级恰差
  {rebalance_anchor, output_dir}，且该级以复核 run 过线为前提
