# v2-ops-cockpit-page（生产运维只读驾驶舱）

## ADDED Requirements

### Requirement: 驾驶舱只读，命令只展示不代跑

本页 MUST 只渲染已落盘工件与只读探针的结果，MUST NOT 触发任何作业、训练、
GPU、轮换或推断，MUST NOT 写入任何文件。运维命令 MUST 以**可复制的纯文本**
呈现，由操作人自行在终端执行。

#### Scenario: 页面不含任何执行/写入面

- **WHEN** 检查 `web/operator_ui/pages/ops_cockpit.py` 源码
- **THEN** 不出现 `JobManager` / `subprocess` / `job_runner` / `import qlib`
  等作业与训练触发面
- **AND** 不出现 `open(` / `write_text` / `write_bytes` / `mkdir` 等写侧 API

#### Scenario: 不可逆命令必须标注

- **WHEN** 页面展示会改写生产 manifest 或不可逆的命令
- **THEN** 该命令 MUST 带显著的不可逆标注，MUST NOT 与只读命令同等呈现

### Requirement: 命令由已解析的部署状态生成

页面展示的命令 MUST 由本页**已解析**的部署状态构造，MUST NOT 印出
`$QUANT_*` 这类环境变量拼写——出单侧之外的脚本并不读这些变量，未设时
操作人的 shell 会把它展开成空串，得到一条静默做错事的「可复制」命令。
无对应环境变量的参数 MUST 以显式占位符呈现，并说明其来源。

#### Scenario: 未设变量的默认部署

- **GIVEN** `QUANT_*` 均未设，页面按文档化默认解析
- **THEN** 命令中出现的是解析后的真实路径，MUST NOT 出现 `$QUANT_*`

#### Scenario: 单模型 opt-out 部署

- **GIVEN** `QUANT_ENSEMBLE_MANIFEST` 显式为 `none`
- **THEN** 晨跑命令为 `--model <已解析模型路径>` 形态
- **AND** MUST NOT 出现 `--ensemble-manifest`，MUST NOT 把 `none` 当路径传入

#### Scenario: 门命令必须显式携带已解析的数据路径

- **GIVEN** 部署覆盖了 `QUANT_PROVIDER_URI` 或 `QUANT_NAMECHANGE_PATH`
- **WHEN** 页面给出 `retrain_gate.py` 的两条 scope 命令
- **THEN** 两条命令 MUST 显式携带 `--provider` 与 `--namechange`，
  取值为本页解析到的路径
- **AND** MUST NOT 依赖 CLI 的硬编码默认值——gate 工件不记录任何数据路径，
  用错 bundle 产出的 PASS 事后无从分辨，却足以授权一次生产轮换

#### Scenario: 现任不可解析时不给出可运行命令

- **GIVEN** 现任指针不可解析
- **THEN** 页面 MUST NOT 给出一条指向该 manifest 的可运行命令——
  那等于交给操作人一条用无法确认之模型出单的命令

### Requirement: 现任身份由两页共用的同一解析器给出

驾驶舱与今日推荐页 MUST 通过**同一个**现任解析器取得生产模型身份，
MUST NOT 各自实现一份。未设指针 MUST NOT 被推断为单模型；指针已设但校验器
拒绝时 MUST 醒目 WARN，MUST NOT 回退为单模型形态或占位值。

#### Scenario: 两页答案不可能分歧

- **WHEN** 检查两页取得现任身份的路径
- **THEN** 二者解析到同一个共享模块的同一函数

#### Scenario: 现任不可解析

- **GIVEN** 现任指针已设但规范校验器拒绝
- **THEN** 驾驶舱醒目 WARN 并给出拒绝原因，MUST NOT 显示任何单模型或占位身份

### Requirement: gate 状态的权威是入库 baseline 的摘要，不是 gate 文件本身

gate 卡片 MUST 先用 `docs/promotion/csi800_n5_bootstrap_baseline.json` 的
`authorized_by.gate_artifacts[*].sha256` 校验所读工件的实际内容摘要，
校验通过后 MUST 逐字转录工件里的 `overall` 与各具名门 `verdict`，
MUST NOT 重新推导任何 verdict。摘要不符时 MUST 显示证据链断裂，
MUST NOT 显示 PASS。

#### Scenario: 工件内容与授权摘要不符

- **GIVEN** 某份 gate 工件的实际 sha256 ≠ baseline 记录的摘要
- **THEN** 该卡片显示「证据链断裂」并给出两个摘要
- **AND** MUST NOT 显示该工件自称的 `overall`

#### Scenario: 缺门

- **GIVEN** 某作用域的工件缺少 `expected_gates(scope)` 中的某一道门
- **THEN** 页面显示缺门清单，该卡片 MUST NOT 呈现为通过

#### Scenario: 转录的必须是被哈希的那份字节

- **GIVEN** 基线记录路径下的工件在两次读取之间被替换
- **WHEN** 页面渲染该 gate 卡片
- **THEN** 每个候选文件 MUST 只读取一次，摘要与解析 MUST 取自同一块 buffer
- **AND** MUST NOT 出现「摘要按旧字节通过、结论来自新字节」的状态

#### Scenario: 贴边余量必须可见

- **WHEN** `serving_veto` 的任一指标接近其阈值
- **THEN** 页面显示该指标的实测值、阈值与余量，MUST NOT 只显示 PASS

### Requirement: recert 有效期来自执行器的判定函数与被 pin 的同一 rev

recert 状态与 15 个月有效期 MUST 由 `scripts.rotation_lib` 的
`parse_recert_status()` / `recert_validity()` 真跑得出，MUST NOT 手填、
常量化或缓存成事实。读取 MUST 先 pin 一次主线 rev，再用**同一 rev** 读状态
正文与读该路径的 tip commit 日期。页面 MUST 显示所 pin 的 rev。

#### Scenario: 正文与日期同源

- **WHEN** 页面读取 recert 状态
- **THEN** 状态正文与有效期锚日期取自同一个被 pin 的 commit

#### Scenario: 判定所用的时钟必须与执行器一致

- **GIVEN** `recert_validity` 以 `now.date()` 比较到期日且不做时区归一，
  而轮换执行器传入的是 UTC 瞬时
- **WHEN** 页面判定有效期
- **THEN** MUST 使用同一 UTC 时钟，MUST NOT 传入本地时区瞬时——
  否则在到期日边界上，页面会与执行器给出相反的结论

#### Scenario: git 探针不可用

- **GIVEN** 本机 `origin/main` 不可解析或 git 探针失败
- **THEN** 页面显式显示「无法判定」及原因，MUST NOT 显示上一次的结果，
  MUST NOT 默认为有效

### Requirement: 季度重训窗口 MUST 标明由间距硬 pin 推导

页面 MUST 把下一名成员 `fit_end` 的可接受窗口显示为
`[最新成员 fit_end + MEMBER_SPACING_DAYS_MIN, 最新成员 fit_end +
MEMBER_SPACING_DAYS_MAX]`，并 MUST 写明这是由 serving 校验器的间距硬 pin
**推导**所得。本仓库不存在「下次重训到期日」的机器可读锚，页面
MUST NOT 呈现一个看起来像仓库事实的到期日。

#### Scenario: 窗口已关闭

- **GIVEN** 今天晚于 `最新成员 fit_end + MEMBER_SPACING_DAYS_MAX`
- **THEN** 页面显示窗口已关闭及关闭天数
- **AND** 显示「用今天的数据训，间距为 N 天，超出上限，serving 校验器会拒绝」

#### Scenario: 不得伪造到期日

- **WHEN** 检查页面文案
- **THEN** 窗口一律标注为由间距硬 pin 推导
- **AND** 不出现任何未经推导说明的「下次重训到期日」

### Requirement: 数据新鲜度复用既有读取器与既有阈值

页面 MUST 用 `bundle_health.summarise_bundle_health()` 取 bundle 尾部日期，
用 `RecommendationConfig.bundle_max_age_days` 作为拒绝阈值，
MUST NOT 新造第二个阈值。页面 MUST 写明尾部日期取自哪一条读取路径。

#### Scenario: 显示落后天数与余量

- **WHEN** bundle 尾部日期可读
- **THEN** 显示尾部日期、落后天数、距拒绝阈值的余量

#### Scenario: 阈值不得硬编码

- **WHEN** 检查页面与其 helper 源码
- **THEN** 拒绝阈值来自 `RecommendationConfig` 的字段，而非页面里的字面量
