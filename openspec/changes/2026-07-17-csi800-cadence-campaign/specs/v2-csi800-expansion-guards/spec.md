## ADDED Requirements

### Requirement: 生产者 SHALL 在 fold report 与 manifest 中对 positions 盖内容摘要

walk-forward 引擎写盘每折时，SHALL 对**已持久化的** positions JSON
字节计算 sha256，并以 `positions_sha256` 字段写入该折 fold report 与
manifest（fold report schema 版本升级）。摘要 SHALL 在 positions 文件
写盘之后、对同一字节流计算（写什么盖什么）；positions 未产出的失败折
SHALL NOT 携带该字段。此为 #373 codex r10 预留晋升档位
`producer_digest_certified` 的生产者侧前置。

#### Scenario: 摘要与盘面字节一致
- **WHEN** 一折完成且 positions 已写盘
- **THEN** 该折 fold report 的 `positions_sha256` 等于对盘面
  `fold_XX_positions.json` 字节重新计算的 sha256

#### Scenario: 失败折不携带摘要
- **WHEN** 一折被约束中止未产出 positions
- **THEN** 该折无 fold report（现状）或其记录不含 `positions_sha256`，
  聚合照常以 NaN 占位

### Requirement: 配对工件 v3 SHALL 将参照 run 作为第三方入证

`csi800_campaign_pair_report.py` SHALL 升级 schema 至
`csi800_pair_report_v3`：除 base/conservative 双侧外，SHALL 接受并
认证参照 run（`--reference-run`），以与双侧同款的四件套入档——
`run_id`、`config_sha256`、`report_sha256`、`fold_report_sha256`
（逐折，失败折除外）。参照的配置绑定沿既有钉死差集（对 base 投影
diff 恰 `{instruments, benchmark_code, attribution_sleeve_grouping}`）。
attach 步骤 SHALL 据 v3 条目验证参照 fold report 哈希后再消费其
positions 证据；pre-v3 工件 SHALL 被 attach 拒绝（防降级剥离）。

#### Scenario: 参照 fold report 配对后被改
- **WHEN** pair v3 生成后参照某折 fold report 被替换，随后运行 attach
- **THEN** attach 以哈希失配拒绝（"changed after pairing"语义）

### Requirement: attach SHALL 验证全链摘要并在达标时授予 producer_digest_certified

attach 工装 SHALL 对三 run 的每个完成折验证摘要链：pair v3 条目 →
fold report 字节哈希 → fold report 内 `positions_sha256` → 盘面
positions 字节哈希。任何一环缺失或失配 SHALL 按既有语义处理（缺
`positions_sha256` = 未认证，维持 `window_only_unauthenticated` 与
晋升 block；哈希失配 = 撕裂证据，拒绝）。既有的窗口绑定、内嵌换手
交叉验证、去重、非有限值防线 SHALL 全部保留（摘要链是追加防线非
替代）。

**不可变锚前置与 verdict 侧车（codex #374 r3+r4）**：摘要链的根
（pair v3 文件）在 attach 时与其余工件同盘可变——伪造全套自洽证据后
重生成 v3 即可使链内一切一致；且 attach 会把勾验回写进 pair 工件，
认证后该文件必然偏离 HEAD、其内嵌资格字段本身无锚。故 SHALL 采用
**强制两件套**（非可选机械形态）：

1. **attach（工作树步骤）**：照常回写勾验与诊断到 pair 工件，但其
   内嵌 `promotion_eligible` SHALL 恒为 false 并携带 unauthenticated
   blocker——pair 工件内嵌资格字段 SHALL NOT 是晋升权威，任何下游
   SHALL NOT 据其放行；
2. **certify（认证步骤，SHALL NOT 改写任何已锚工件）**：验证
   (a) 所消费 pair v3 字节与 git 已提交版本（钉死 repo 路径@HEAD）
   逐字节一致，(b) 全摘要链对盘面成立，(c) 五项 veto 与主判据——
   全部通过时产出**独立 verdict 侧车**（钉死路径），载有被锚 pair
   v3 的 sha256、锚验证时的 commit id、链验证结果与晋升判定。
   `promotion_eligible=true` SHALL 仅以"已提交的 verdict 侧车 + 其
   记录的 pair digest 与已提交 pair v3 一致"这一组合形态存在。

顺序 SHALL 不可倒置：run → attach → pair v3 提交评审 → certify →
侧车提交评审 → 晋升。仅存在于工作树、未经提交评审的任一环 SHALL NOT
达成晋升。实现与测试（含"certify 不改写已锚工件"“侧车 digest 断链
拒绝"用例）在 PR-B。

#### Scenario: 全链达标且过不可变锚晋升门开
- **WHEN** 三 run 全部由 attestation 生产者产出、盘面未被动过、pair
  v3 已提交且字节与 HEAD 一致，certify 全链验证通过、五项 veto 全不
  触发
- **THEN** certify 产出 verdict 侧车（载被锚 pair digest + commit
  id），侧车提交评审后晋升成立；pair 工件本体未被 certify 改写

#### Scenario: 工作树工件铸不出资格
- **WHEN** 摘要链全链一致但所消费的 pair v3 仅存在于工作树（与
  HEAD 已提交字节不一致或路径未纳管）
- **THEN** certify 拒绝产出侧车；attach 回写的 pair 工件内嵌资格
  恒为 false 并携带 unauthenticated blocker

#### Scenario: 侧车与已锚 pair 断链被拒
- **WHEN** verdict 侧车记录的 pair digest 与已提交 pair v3 的字节
  哈希不一致（任一侧被替换）
- **THEN** 下游按断链拒绝该晋升判定

#### Scenario: positions 被换而摘要不可复现
- **WHEN** 任一完成折的 positions 被替换（摘要链任一环失配）
- **THEN** attach 拒绝，不产出勾验结果

### Requirement: CSI800 降换手战役 SHALL 按预注册双条件与既有 veto 表判定

N5 降换手战役 SHALL 以三发新 run 执行：csi300 N5 参照、csi800 N5
base（5 bps）、csi800 N5 conservative（20 bps），三发 SHALL 统一
`rebalance_cadence_days=5`、`rebalance_phase=0`、
`rebalance_anchor="fold_phase"`（显式写入 preset，禁试后调 N——
N∈{1,5}，改 N 须新 OpenSpec 变更并作废既有结果）。N=1 对照臂 SHALL
复用 #373 已证工件，不重跑。

判定 SHALL 为预注册双条件（AND，跑前冻结）：

1. conservative（20 bps）N5 全窗净超额年化 **> 0**；
2. **毛塌缩否决**：N5 毛超额年化 ≥ N1 毛超额年化 × **50%**
   （毛口径 = 逐折 `excess_return_without_cost.annualized_return`
   跨折均值，双方同法计算）。

两条同过 = WIN（进入晋升流程，仍须过五项 veto 与 attestation 晋升
门）；任一不过 = 如实入档不晋升。五项 veto 数字沿用 #368/#372 原样。

**N1 基线 SHALL 钉进认证链（codex #374 r1）**——50% 比较的 N1 毛值
SHALL NOT 取自任何可编辑文档（简报/手抄数字）：

- 已提交的 #373 配对工件（v2）钉有 N1 base run 身份（run_id
  `csi800_campaign_base`、`report_sha256`、逐折 `fold_report_sha256`），
  毛值本体在被这些哈希钉住的 fold report 内
  （`backtest.risk_analysis.excess_return_without_cost`）；
- PR-B SHALL 将 N1 配对工件以 v3 **重生成**：新增逐折毛超额记录，
  且 v2 已钉的全部哈希（双侧 run_id/config_sha256/report_sha256/
  fold_report_sha256）SHALL 逐字段不变（治理断言）——盘面 run 目录
  不变则重生成不改变认证内容，只是把毛值抬进认证工件；
- 主判据比较工装 SHALL 仅消费已提交的 v3 工件（N1 与 N5 两侧），
  N1 逐折毛值缺失、哈希与盘面失配、或官方折覆盖不全（双侧各须
  23/23）一律拒绝；
- 折网格对齐由构造保证：N5 preset 与 N1 的 walk-forward 窗口/步长
  配置 SHALL 恰同（治理 diff pin 仅容 cadence 三字段 + output_dir
  差异），毛均值 = 各自全部官方折的跨折均值；
- N1 run 目录 SHALL 保持完好直至战役收束——丢失即 fail-closed
  （基线不可重建，战役判定无效），SHALL NOT 以文档数字替代。

#### Scenario: N1 基线被换或事后修改
- **WHEN** 主判据比较时提供的 N1 工件哈希与 #373 已提交 v2 所钉
  不一致，或其逐折毛值与盘面 hash 验证后的 fold report 不符
- **THEN** 比较工装拒绝，战役判定不产出

#### Scenario: 省成本靠杀 alpha 被否
- **WHEN** N5 conservative 净超额 +0.5% 但 N5 毛超额仅为 N1 的 40%
- **THEN** 毛塌缩否决触发，判 LOSE 入档，不进入晋升流程

#### Scenario: 试后调 N 被拒
- **WHEN** N5 结果不利，有人提议改跑 N=3 或 N=10
- **THEN** 按本契约拒绝——节奏参数是预注册值，修改须走新 OpenSpec
  变更并作废既有战役结果

## MODIFIED Requirements

### Requirement: 虚高 veto 表跑前钉死且任一触发即否决晋升

（本次仅修订 veto③ 的参照定义——"同配置"明确含 cadence 三字段；
其余四条原文与数字照录不变（含 #372 选项 A 的 veto④ 校准修订），
修订于 N5 战役零结果窗口内，2026-07-17。MODIFIED 全文重述以保持
canonical spec 完整——codex #374 r2。）

每个 csi800 战役决策 run SHALL 逐条对照下表勾验，任一触发 SHALL 否决
晋升（该 run 可作诊断继续分析，但不得作为晋升依据）。判据与数字
SHALL 先于任何战役数据存在，跑后 SHALL NOT 修改。五条判据：

1. **conservative 净超额**：conservative 档（20 bps）对 SH000906TR 的
   全窗净超额年化 ≤ 0 → veto。
2. **CSI500-sleeve 依赖度**：sleeve 报告中 csi500 sleeve 贡献占毛超额
   比例 ≥ 80% **且** conservative 档净超额 ≤ 0 → 判定"虚高（低估
   illiquidity 冒充 breadth）"→ veto（工单红旗判据的量化形态）。
3. **换手（本次修订）**：csi800 run 的年化单边换手 > **同配置** csi300
   参照 run 的 1.5 倍 → veto（breadth 不得靠制造换手兑现）。"同配置"
   SHALL 包含 `rebalance_cadence_days`/`rebalance_phase`/
   `rebalance_anchor` 三字段——N5 战役的 veto③ 参照 = csi300 N5
   参照；对日频参照比较周频 run 的比率无意义地趋零，会使 veto③
   失去牙齿，SHALL NOT 采用。
4. **单票集中度与杠杆**（校准修订：选项 A，2026-07-17 操作人签，修订
   时零战役结果存在）：run 必须以 **campaign 校准**
   （`campaign_risk_constraints_v1`）在 runtime 强制执行——
   **max_per_name = 0.05 与 max_leverage = 1.0 严格（RAISE 模式），不得
   放宽**；max_per_board = 1.0（禁用——board_heuristic 桶是上市板块非
   风险行业，沪主板独占指数篮子权重约半，首发点火实证 23/23 折
   53-60% 结构性"违规"）；cash_buffer_min = 0.0（qlib topk 策略满仓
   设计，实证现金 0.55-0.9%；现金缓冲属实盘部署关切非回测有效性）。
   生效值 SHALL 记录进 run 工件供勾验；约束未接线、未记录、或
   max_per_name / max_leverage / mode 被改动的 run 一律无效。校准值
   由治理测试钉死，再改仍须新 OpenSpec 变更。
5. **中盘集中度**：csi500 sleeve 的时均组合权重 > 75%，或 sleeve 报告
   `unknown` 桶时均权重 > 10% → veto（宇宙退化为中盘单边注 / 分组图
   失真，probe 实证基线 61.8% / 4.4%）。

#### Scenario: 依赖度红旗触发
- **WHEN** 某战役 run 毛超额的 82% 来自 csi500 sleeve 且 conservative
  档净超额为 −0.4%
- **THEN** 判定虚高，该 run 不得进入晋升流程，结论如实入档

#### Scenario: veto 表先于数据
- **WHEN** 第一个战役决策 run 点火之时
- **THEN** 本 veto 表（含全部数字）已在 main 上，且战役报告逐条对照
  勾验

#### Scenario: 风险约束未接线的 run 无效
- **WHEN** 一个战役 run 在 pipeline/walk-forward 未显式传入
  `MinimalRiskConstraints` 默认值（即 `BacktestRunner.run` 以
  `risk_constraints=None` 执行）或其工件缺约束生效值记录
- **THEN** 该 run 判无效，不得进入 veto 勾验与晋升流程

#### Scenario: 参照节奏失配被拒
- **WHEN** N5 战役的 veto③ 勾验被提供 N=1 的 csi300 参照
- **THEN** attach 以参照配置绑定失配拒绝（cadence 三字段在三发内
  必须一致）
