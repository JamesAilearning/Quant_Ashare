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
positions 字节哈希。全链一致时 `reference_content_binding` SHALL 置
`producer_digest_certified`（晋升门可开）；任何一环缺失或失配 SHALL
按既有语义处理（缺 `positions_sha256` = 未认证，维持
`window_only_unauthenticated` 与晋升 block；哈希失配 = 撕裂证据，
拒绝）。既有的窗口绑定、内嵌换手交叉验证、去重、非有限值防线
SHALL 全部保留（摘要链是追加防线非替代）。

#### Scenario: 全链达标晋升门开
- **WHEN** 三 run 全部由 attestation 生产者产出且盘面未被动过，五项
  veto 全不触发
- **THEN** attach 发 `promotion_eligible=true` 且无
  `reference_binding_unauthenticated` blocker

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

#### Scenario: 省成本靠杀 alpha 被否
- **WHEN** N5 conservative 净超额 +0.5% 但 N5 毛超额仅为 N1 的 40%
- **THEN** 毛塌缩否决触发，判 LOSE 入档，不进入晋升流程

#### Scenario: 试后调 N 被拒
- **WHEN** N5 结果不利，有人提议改跑 N=3 或 N=10
- **THEN** 按本契约拒绝——节奏参数是预注册值，修改须走新 OpenSpec
  变更并作废既有战役结果

## MODIFIED Requirements

### Requirement: 虚高 veto 表跑前钉死且任一触发即否决晋升（veto③ 参照口径修订）

（仅修订 veto③ 的参照定义，其余五条原文与数字不变——修订于 N5 战役
零结果窗口内，2026-07-17。）

3. **换手**：csi800 run 的年化单边换手 > **同配置（含
   `rebalance_cadence_days`/`rebalance_phase`/`rebalance_anchor`
   三字段）** csi300 参照 run 的 1.5 倍 → veto。N5 战役的 veto③
   参照 = csi300 N5 参照；对日频参照比较周频 run 的比率无意义地
   趋零，会使 veto③ 失去牙齿，SHALL NOT 采用。

#### Scenario: 参照节奏失配被拒
- **WHEN** N5 战役的 veto③ 勾验被提供 N=1 的 csi300 参照
- **THEN** attach 以参照配置绑定失配拒绝（cadence 三字段在三发内
  必须一致）
