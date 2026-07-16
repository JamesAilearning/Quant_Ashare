## ADDED Requirements

### Requirement: CSI800 战役回测 SHALL 在三道虚高护栏落地后才允许点火

任何以 csi800 宇宙产生"业绩证据"意图的回测（战役决策 run），SHALL 在
本 capability 的三道护栏（成本敏感带、sleeve 分解接线、虚高 veto 表）
全部落地并入 main 之后才允许执行。此前产生的任何 csi800 数字（含只读
probe）SHALL 保持"底数非决策"地位，不得作为业绩证据引用。

#### Scenario: 护栏未齐时的战役点火被拒
- **WHEN** sleeve 接线或 conservative preset 尚未合并进 main，而操作流程
  请求执行一个 csi800 战役决策 run
- **THEN** 该请求按本契约拒绝（研究纪律层，不产生可引用数字）

### Requirement: 战役决策 run SHALL 成对执行成本敏感带并以 conservative 档为主判

每个 csi800 战役决策 run SHALL 以完全相同的配置成对执行两档滑点：
base = 5 bps 与 conservative = **20 bps**（全账本平铺，DP-2 签署值，
跑前写死、跑后不得回调）。晋升与判定 SHALL 以 conservative 档的净超额
为主判；base 档仅作参考并如实并列呈报。canonical 合约的
``slippage_bps`` 单标量语义不变（敏感带是 config 层双 run，非 runtime
变更）；per-instrument 分段滑点成本模型为已记录的 backlog（audit A4
同族），不阻塞本契约。

两档 SHALL 物化为**单一配对战役报告工件**（codex P1 on #368）：同时
载有两档的官方指标、双方 run id，以及双方持久化配置的 **diff 证明**
——配对性由工件自证，不靠口头声明。diff SHALL 按**显式比较投影**执行
（codex P1 on #368 r2）：投影排除一个**显式枚举的 run-identity/输出
位置字段白名单**（`output_dir` 及实现时确认的同类字段——walk-forward
配对的两侧必然使用不同输出目录，全字段 diff 会自破），其余全部
**执行语义字段**除 ``slippage_bps`` 外 SHALL 零差异；排除白名单本身
SHALL 是受治理测试钉死的显式常量，SHALL NOT 借"run-identity"名义收纳
任何执行语义字段。缺任一侧（尤其 conservative 侧）的报告 SHALL 判
无效而非"待补"。veto 勾验 SHALL 消费该配对工件，SHALL NOT 接受任一
单侧 run 报告作为替代——两个独立 run 报告无法证明输入匹配，也无法
阻止不利的 conservative 工件被省略。

#### Scenario: 敏感带成对呈报
- **WHEN** 一个 csi800 战役决策 run 完成
- **THEN** 配对报告工件同时载有 base 与 conservative 两档的净超额与
  双方 run id + 配置 diff 证明，且主判结论引用 conservative 档

#### Scenario: 省略不利的 conservative 侧被拒
- **WHEN** 只提交 base 档 run 报告（conservative 工件缺失，或投影后
  config diff 含 slippage_bps 之外的执行语义差异）请求进入晋升流程
- **THEN** 该报告按本契约判无效，veto 勾验拒绝受理

#### Scenario: run-identity 字段不误伤真实配对
- **WHEN** 一对 walk-forward base/conservative run 仅在 `output_dir`
  （白名单内的 run-identity 字段）与 `slippage_bps` 上不同
- **THEN** 配对报告正常生成——投影排除白名单字段后 diff 恰为
  slippage_bps 一处

#### Scenario: conservative 幅度不可试后回调
- **WHEN** conservative 档结果不利，有人提议把 20 bps 下调后重跑
- **THEN** 按本契约拒绝——幅度是预注册值，修改须走新的 OpenSpec 变更
  并作废既有战役结果

### Requirement: csi800 运行 SHALL 输出 CSI300/CSI500 sleeve 分解报告

csi800 战役 run 的 attribution SHALL 按 `csi800_sleeve_v1` 分组
（`src/core/attribution_sleeve_loader.resolve_sleeve_map`，as-of 为评估
窗首日）输出 per-sleeve 的组合权重、基准权重、组合腿收益、基准腿收益
与（walk-forward 路径下）per-sleeve 换手；接线经**显式 config 键**进入
attribution 层，与 industry 分组源互斥（同 run 只允许一种分组来源）；
loader 的覆盖界守卫（per-sleeve、越界拒绝）SHALL fail-loud 透传，不得
静默降级。sleeve 报告是诊断层，SHALL NOT 改变官方超额数字。

#### Scenario: sleeve 分组与 industry 分组互斥
- **WHEN** 配置同时声明 sleeve 分组与 industry taxonomy artifact
- **THEN** 配置校验拒绝该组合（fail-loud），不静默择一

#### Scenario: 覆盖界越界透传
- **WHEN** 评估窗首日超出任一 sleeve 的成分快照覆盖界
- **THEN** run 以 SleeveResolutionError 语义失败并指引重解析成分快照，
  不产出无 sleeve 报告的"裸"业绩数字

### Requirement: 虚高 veto 表跑前钉死且任一触发即否决晋升

每个 csi800 战役决策 run SHALL 逐条对照下表勾验，任一触发 SHALL 否决
晋升（该 run 可作诊断继续分析，但不得作为晋升依据）。判据与数字
SHALL 先于任何战役数据存在（本 change 合并即满足），跑后 SHALL NOT
修改。五条判据（DP-4，2026-07-16 操作人签）：

1. **conservative 净超额**：conservative 档（20 bps）对 SH000906TR 的
   全窗净超额年化 ≤ 0 → veto。
2. **CSI500-sleeve 依赖度**：sleeve 报告中 csi500 sleeve 贡献占毛超额
   比例 ≥ 80% **且** conservative 档净超额 ≤ 0 → 判定"虚高（低估
   illiquidity 冒充 breadth）"→ veto（工单红旗判据的量化形态）。
3. **换手**：csi800 run 的年化单边换手 > 同配置 csi300 参照 run 的
   1.5 倍 → veto（breadth 不得靠制造换手兑现）。
4. **单票/板块集中度**：run 必须以既有 `MinimalRiskConstraints` 默认值
   在 **runtime 强制执行**（max_per_name = 0.05、max_per_board = 0.40，
   不得放宽），且生效值 SHALL 记录进 run 工件供勾验。当前 pipeline /
   walk-forward 调 `BacktestRunner.run` 时**不传 `risk_constraints`**
   （缺省=无仓位级约束，codex P1 on #368）——战役实现 SHALL 补显式
   接线；约束未接线、未记录或被放宽的 run 一律无效。
5. **中盘集中度**：csi500 sleeve 的时均组合权重 > 75%，或 sleeve 报告
   `unknown` 桶时均权重 > 10% → veto（宇宙退化为中盘单边注 / 分组图
   失真，probe 实证基线 61.8% / 4.4%）。

#### Scenario: 依赖度红旗触发
- **WHEN** 某战役 run 毛超额的 82% 来自 csi500 sleeve 且 conservative
  档净超额为 −0.4%
- **THEN** 判定虚高，该 run 不得进入晋升流程，结论如实入档

#### Scenario: veto 表先于数据
- **WHEN** 第一个战役决策 run 点火之时
- **THEN** 本 veto 表（含全部数字）已在 main 上（本 change 合并即满足），
  且战役报告逐条对照勾验

#### Scenario: 风险约束未接线的 run 无效
- **WHEN** 一个战役 run 在 pipeline/walk-forward 未显式传入
  `MinimalRiskConstraints` 默认值（即 `BacktestRunner.run` 以
  `risk_constraints=None` 执行）或其工件缺约束生效值记录
- **THEN** 该 run 判无效，不得进入 veto 勾验与晋升流程

### Requirement: csi800 战役宇宙 SHALL 保留金融股（与现役一致）

csi800 战役（Alpha158 breadth 杠杆）SHALL 使用完整 csi800 成分宇宙，
**不排除金融股**：`ex_financials` 是质量因子战役的口径（金融股盈利
因子不可比），价量模型不适用该理由；现役 csi300 生产模型与 (b) probe
双边均含金融。与质量战役口径的差异是有意的、就此记录。未来若任何
csi800 变体提出金融排除，SHALL 作为相对现役的显式偏离给出经济理由并
走独立 OpenSpec 变更。

#### Scenario: 机械照搬质量口径被拒
- **WHEN** 一个 csi800 战役配置无经济理由地声明金融排除
- **THEN** 按本契约拒绝——排除口径变更须独立提案

