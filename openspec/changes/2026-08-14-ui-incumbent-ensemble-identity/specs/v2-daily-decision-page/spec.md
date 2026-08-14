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
