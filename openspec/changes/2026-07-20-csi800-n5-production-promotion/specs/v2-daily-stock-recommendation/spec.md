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

任何替换 canonical 生产模型（pkl + meta）的晋升执行 SHALL 满足
全部前置，缺一即拒绝执行——**零写入的范围限于晋升执行本体**
（canonical pkl/meta 替换、备份件、基线记录），失败路径的审计
记录（guard eval 产物、如实入档文本）SHALL 照常写入，二者不
冲突（失败必须留痕，canonical 必须不动）：

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
3. **候选 guard eval 硬 veto**（frozen 模型、csi800/SH000906TR/
   N5/20 bps 口径、guard 窗跑前钉死）：0 degenerate days、
   0 cutoff-straddle days、净超额年化 > 0，且
   `v2-csi800-expansion-guards` 五 veto 数字原样适用；任一不过
   SHALL 如实入档并中止晋升；
4. **回滚件义务**：替换前 SHALL 写 pre-promote 备份（pkl + meta，
   带时间戳）并在 `docs/promotion/` 落新基线记录，现任基线保留；
   回滚 SHALL 为恢复备份件的单步操作。

guard eval 的全部数字 SHALL 于晋升执行 PR 的数字 STOP 首次呈报，
跑后 SHALL NOT 修改判据或数字。

#### Scenario: 侧车缺失或复验失败时拒绝晋升

- **WHEN** 晋升工具在无已提交侧车、或 `--verify` 失败、或
  `promotion_eligible != true` 的状态下被调用
- **THEN** 拒绝执行，canonical 工件（pkl/meta/备份/基线）零
  写入，失败原因记录写入审计档，报错指向缺失的前置

#### Scenario: guard eval 任一硬 veto 触发

- **WHEN** 候选在 guard 窗出现 degenerate day 或净超额 ≤ 0
- **THEN** 晋升中止、guard eval 产物与结果如实入档、现任
  canonical 零写入不动

#### Scenario: 未锚定的 iso_week 复核输出被拒绝

- **WHEN** 晋升工具被指向一个仅存在于本地工作树（未提交/未
  合并主线）的 iso_week 复核 report
- **THEN** iso_week 复核门拒绝通过，报错指向锚定义务，canonical
  零写入

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
