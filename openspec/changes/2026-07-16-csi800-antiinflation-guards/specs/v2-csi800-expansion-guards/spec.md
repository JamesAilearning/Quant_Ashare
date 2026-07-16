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

#### Scenario: 敏感带成对呈报
- **WHEN** 一个 csi800 战役决策 run 完成
- **THEN** 其报告同时载有 base 与 conservative 两档的净超额，且主判
  结论引用 conservative 档

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
   执行（max_per_name = 0.05、max_per_board = 0.40，不得放宽）；任何
   放宽即 run 无效。
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

