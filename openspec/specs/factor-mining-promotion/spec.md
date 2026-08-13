# factor-mining-promotion Specification

## Purpose

一个被 FWER 机制认证的候选因子，凭什么、以及在什么条件下，可以被拿去
和生产模型族做**决策级**的配对比较。

本 spec 管的是那条**授权链**，而不是因子好坏：晋升 bundle 只受台账里
已签署的裁决约束（谁有资格、注册的是谁）；绑定它的 run 必须能证明自己
用的就是那个 bundle 和那批数据；裁尺只对**注册过的**臂型出判决。链上
任何一环拿不出证据，就必须拒绝，而不是给出一个看起来合理的判决。

首个消费者是 pv_incremental_v1 的晋升门（PV-DP-7 步 2-4，台账
E007-E009）；其结论 INDISTINGUISHABLE → 不晋升，本身即该链路的一次
完整实证。
## Requirements
### Requirement: 代表 bundle 只受 E007 幸存名单裁决

单代表生产 bundle 工具 MUST 以"candidate_id 在 E007 幸存名单内"为
唯一晋升判据，MUST NOT 引入或复用 v1/D4 旧判据
（`src/factor_mining/promote.py` 的 ValidationCriteria）。

#### Scenario: 幸存者通过

- **GIVEN** E007 verdict 幸存名单含 `pv001_2789e60e`
- **WHEN** 工具以该 id 运行
- **THEN** 单条目池 + provenance sidecar 落盘（verdict sha、manifest
  sha、GP run 指纹、orientation、表达式全文）

#### Scenario: 非幸存者拒绝

- **WHEN** 工具以不在幸存名单的 id 运行
- **THEN** 拒绝且不落盘任何工件

#### Scenario: 裁决文件被篡改拒绝

- **WHEN** `--verdict` 文件 sha256 与台账 E007 记录值不符
- **THEN** 拒绝且指明期望/实际摘要

### Requirement: Alpha158PlusMined 两臂 label 逐字节一致

组合 handler MUST 输出 Alpha158 特征列 ∪ mined 因子列，且 label
表达式与 Alpha158 缺省逐字节相同；treatment/baseline 两臂在同一
config 下的 label MUST 相等。

#### Scenario: 拼列完整性

- **WHEN** 以单因子 bundle 构建 Alpha158PlusMined 数据集
- **THEN** 列集 = Alpha158 列 ∪ {因子列}，label 列与 Alpha158 缺省
  逐字节一致

### Requirement: 配对 run 判定窗独占 OOS dev

双臂 preset MUST 钉 `overall_start: 2020-10-01`、
`overall_end: 2024-12-31`（首 test 窗恰为 2023-01-01，判定折全部
落在 OOS dev 2023-2024），MUST 走 canonical 口径（不设
`risk_constraints_mode` / `metrics_purpose` 键），两 preset 逐键
diff MUST 仅为 feature_handler（及其 bundle 绑定键）。

#### Scenario: 治理钉核对 preset

- **WHEN** 治理测试加载两 preset
- **THEN** 上述每项键值断言成立，任一漂移则红

### Requirement: 净基差判据 = 裁尺三态 verdict 原样

净基差改善 MUST 由 run-comparison ruler 的决策级路径
（`--prereg-plan docs/prereg/pv_promotion_paired.yaml --variant`）
出具三态 verdict 裁定，MUST NOT 另设第二判据或数字门槛；
`TREATMENT_BETTER` 之外的任何结果 MUST 照录台账且不晋升。

#### Scenario: plan 单变体冻结

- **WHEN** 治理测试加载 prereg plan
- **THEN** treatments 恰为 `["alpha158-plus-pv001"]` 单变体

