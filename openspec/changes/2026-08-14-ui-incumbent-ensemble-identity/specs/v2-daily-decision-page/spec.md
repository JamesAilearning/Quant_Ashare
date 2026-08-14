# v2-daily-decision-page（现任 ensemble 身份）

## ADDED Requirements

### Requirement: 横幅描述现任形态，认不出即 WARN

今日推荐页顶的「现任生产模型」横幅 MUST 反映生产**当前实际服务的形态**。
当现任为 ensemble 时，横幅 MUST 显示 manifest 身份（文件名 + sha256 +
成员数 + 各成员 fit 窗）而非任何单模型。现任指针已设但不可解析时，页面
MUST 醒目 WARN，MUST NOT 回退为单模型形态、MUST NOT 显示占位值。

#### Scenario: 现任为 ensemble

- **GIVEN** `QUANT_ENSEMBLE_MANIFEST` 指向一份可读的生产 manifest
- **WHEN** 页面渲染横幅
- **THEN** 显示该 manifest 的文件名、sha256、成员数与各成员 fit 窗

#### Scenario: 现任指针不可解析

- **GIVEN** `QUANT_ENSEMBLE_MANIFEST` 已设但文件缺失或非法
- **THEN** 页面醒目 WARN，且不显示任何单模型或占位身份

#### Scenario: 未设指针走文档化默认，MUST NOT 推断为单模型

- **GIVEN** 未设 `QUANT_ENSEMBLE_MANIFEST`
- **THEN** 按文档化默认（生产 manifest 路径）解析现任；
  MUST NOT 因变量缺席就断定生产为单模型形态

#### Scenario: 现任为单模型（显式 opt-out）

- **GIVEN** `QUANT_ENSEMBLE_MANIFEST` 显式设为 `none`
- **THEN** 维持既有的晋升 meta 横幅语义，包括「缺字段只进 WARN、
  绝不填默认值」

### Requirement: ensemble 工件对现任 manifest 交叉核对

选中的工件由 ensemble 生成时，页面 MUST 将其
`meta.ensemble.manifest_sha256` 与现任 manifest 的实际 sha256 比对，并
MUST NOT 声称「当前生产为单模型形态」这类与现任形态不符的陈述。

#### Scenario: 出自现任 manifest

- **WHEN** 工件的 manifest sha256 等于现任 manifest 的 sha256
- **THEN** 页面告知该清单出自现任 manifest

#### Scenario: 出自另一份 manifest

- **WHEN** 两者不等
- **THEN** 醒目 WARN——与单模型侧 sha 不符同一告警类别

#### Scenario: 现任不可解析时不得静默跳过

- **WHEN** 现任 manifest 无法解析
- **THEN** 页面 WARN 说明无法完成核对，MUST NOT 静默略过该核对

### Requirement: 现任指针只服务于读侧

`QUANT_ENSEMBLE_MANIFEST` MUST 在环境变量文档中登记。出单侧
（`scripts/daily_recommend.py` 的 `--ensemble-manifest`）MUST 保持显式
必传，MUST NOT 因本 change 获得隐式默认值。

#### Scenario: CLI 不获得隐式默认

- **WHEN** 检查 `scripts/daily_recommend.py`
- **THEN** `--ensemble-manifest` 仍无默认值，选择模型仍须操作人显式指定

### Requirement: 现任 × 工件 的来源判定必须穷举

页面对「现任形态 × 工件形态」的来源判定 MUST 覆盖全部组合，MUST NOT 存在
落空的组合。未知输入 MUST 报错，未渲染的裁定 MUST 醒目报错——**静默即等于
把未经核对的工件呈现为「已核对」**，这正是本页要防的失效。

特别地，现任不可解析时，单模型形态的 v2 工件 MUST NOT 回落到**已退役**单
模型 sidecar 的 sha 比对：那条路径在 sha 相符时不输出任何提示。

#### Scenario: 现任不可解析 × 单模型形态工件

- **GIVEN** 现任指针不可解析，且选中工件是自描述的 v2 单模型工件
- **THEN** 页面 WARN 说明无法核对
- **AND** MUST NOT 与已退役单模型的 sidecar 比对，
  MUST NOT 因该 sha 相符而不出声

#### Scenario: 组合穷举与未知输入

- **WHEN** 检查来源判定
- **THEN** 现任 3 态 × 工件 4 形共 12 个组合均有明确裁定
- **AND** 未知的形态输入 MUST 抛错，MUST NOT 落入「恰好排在最后」的分支
