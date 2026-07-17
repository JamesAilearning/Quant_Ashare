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
   (a) **主线锚**——所消费 pair v3 字节 SHALL 与某个**从受保护主线
   （`origin/main`）可达的 commit** 上该钉死路径的字节逐字节一致
   （`git show <anchor>:<path>` 口径；HEAD 一致不充分——feature
   分支上先 commit pair、certify、再连侧车一并 merge 可绕过
   "pair 先 merge 评审"门，codex #374 r6），锚 commit id 记入侧车；
   (b) 全摘要链对盘面成立，(c) 五项 veto 与主判据——全部通过时产出
   **独立 verdict 侧车**（钉死路径），载有被锚 pair v3 的 sha256、
   主线锚 commit id、链验证结果与晋升判定。
   `promotion_eligible=true` SHALL 仅以下列**全部**条件成立的组合
   形态存在（codex #374 r7：侧车自身同样必须主线锚定，否则手写侧车
   引用真锚 pair 即可伪造判定）：
   - verdict 侧车字节 SHALL 与某个 `origin/main` 可达 commit 上其
     钉死路径的字节一致（侧车自身主线锚）；
   - 侧车记录的 pair digest SHALL 与其记录的主线锚 commit 上的
     pair v3 字节哈希一致；
   - 下游复核 SHALL NOT 仅信侧车断言——SHALL 以 certify 的验证模式
     对侧车记录的锚与链**重算复验**（certify 是确定性计算，复验
     失败即判定无效）。

顺序 SHALL 不可倒置：run → attach → pair v3 提交评审 → certify →
侧车提交评审 → 晋升。仅存在于工作树、未经提交评审的任一环 SHALL NOT
达成晋升。实现与测试（含"certify 不改写已锚工件"“侧车 digest 断链
拒绝"用例）在 PR-B。

#### Scenario: 全链达标且过主线锚晋升门开
- **WHEN** 三 run 全部由 attestation 生产者产出、盘面未被动过、pair
  v3 已并入受保护主线且字节与主线锚 commit 一致，certify 全链验证
  通过、五项 veto 全不触发
- **THEN** certify 产出 verdict 侧车（载被锚 pair digest + 主线锚
  commit id），侧车提交评审后晋升成立；pair 工件本体未被 certify
  改写

#### Scenario: 工作树或未并主线的工件铸不出资格
- **WHEN** 摘要链全链一致但所消费的 pair v3 仅存在于工作树或仅在
  feature 分支 commit（不与任何 `origin/main` 可达 commit 上的字节
  一致）
- **THEN** certify 拒绝产出侧车；attach 回写的 pair 工件内嵌资格
  恒为 false 并携带 unauthenticated blocker

#### Scenario: 侧车与已锚 pair 断链被拒
- **WHEN** verdict 侧车记录的 pair digest 与其记录的主线锚 commit 上
  pair v3 的字节哈希不一致（任一侧被替换）
- **THEN** 下游按断链拒绝该晋升判定

#### Scenario: 手写侧车引用真锚被拒
- **WHEN** 一份未经 certify 产出的侧车（仅在 feature 分支或工作树，
  或内容与 certify 复验重算不符）引用了真实主线锚 pair 的 digest 并
  自称判定通过
- **THEN** 下游因侧车自身无主线锚或复验失败而拒绝

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

**N1 基线 SHALL 钉进认证链（codex #374 r1，r7 修订为证据先行入库）**
——50% 比较的 N1 毛值 SHALL NOT 取自任何可编辑文档（简报/手抄数字），
且其提供方式 SHALL NOT 依赖仅存在于单机的 run 目录（fresh checkout/
CI 无 `output/` 工件，"到时重生成"不可执行也不可验证）：

- 已提交的 #373 配对工件（v2）钉有 N1 双侧 run 身份（run_id、
  `report_sha256`、逐折 `fold_report_sha256`），毛值本体在被这些
  哈希钉住的 fold report 内
  （`backtest.risk_analysis.excess_return_without_cost`）；
- PR-B SHALL 将 N1 双侧全部 46 个 fold report **源文件本体**提交至
  钉死证据目录（实测共 ~1.1 MB；目录以 `.gitattributes` 标记
  `-text` 保证字节保真，防换行规范化破坏哈希）——已锚源工件自身
  承载毛值（codex #374 r8：单独的"毛值证据工件"无法在 fresh
  checkout 复验值的来源，哈希一致只证明抽取器验过哈希，不证明值
  取自那些 report）；治理测试 SHALL 逐折断言已提交源文件的 sha256
  == 已提交 v2 工件所钉 `fold_report_sha256`（两侧均为已提交文件，
  CI 端到端可验）；
- 主判据比较工装 SHALL 仅从**哈希验证通过的已提交源 fold report**
  读取 N1 毛值，与 N5 pair v3（原生记录逐折毛值，其源 report 由
  attestation 链锚定）比较；任一缺失、哈希断链、或官方折覆盖不全
  （各须 23/23）一律拒绝；
- 折网格对齐由构造保证：N5 preset 与 N1 的 walk-forward 窗口/步长
  配置 SHALL 恰同（治理 diff pin 仅容 cadence 三字段 + output_dir
  差异），毛均值 = 各自全部官方折的跨折均值；
- **比较臂 SHALL 钉死（codex #374 r7 P2）**：50% 比较 = N5
  conservative 臂毛均值 vs N1 conservative 臂毛均值
  （conservative-to-conservative）；同时 SHALL 校验各自 pair 内
  base 与 conservative 的毛均值相对差 ≤ 5%（毛口径成本无关、同种子
  同预测，实测 N1 为 12.58 vs 12.59；超差 = 证据异常，fail-closed
  拒绝判定）。

#### Scenario: N1 基线证据断链
- **WHEN** 任一已提交 N1 源 fold report 的 sha256 与已提交 v2 工件
  所钉不一致（含换行规范化等任何字节改动），或逐折毛值缺失/覆盖
  不全
- **THEN** 治理测试红 / 比较工装拒绝，战役判定不产出

#### Scenario: 双臂毛值异常发散
- **WHEN** 任一 pair 内 base 与 conservative 的毛均值相对差超过 5%
- **THEN** fail-closed 拒绝判定（毛口径与成本无关，发散即证据异常）

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
