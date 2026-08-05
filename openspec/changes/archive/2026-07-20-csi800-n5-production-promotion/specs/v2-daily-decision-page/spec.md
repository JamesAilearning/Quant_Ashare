# Delta: v2-daily-decision-page — HOLD 工件渲染（codex #385 r5）

## ADDED Requirements

### Requirement: 每日决策页 SHALL 尊重 rebalance_day 语义渲染 HOLD 工件

每日决策页 SHALL 读取推荐工件的 `rebalance_day` 字段并按其语义
渲染：`rebalance_day: false` 的工件 SHALL 以醒目 HOLD 状态展示
（含 `next_rebalance_date`），且入场决策表单 SHALL 被禁用或以
等效方式明确阻断"当作可执行清单操作"的路径；`rebalance_day:
true` 或字段缺失（日频旧工件，向后兼容）时渲染行为与现契约
一致。生产者与 reader 的 HOLD 语义 SHALL 在同一 PR 内同步落地
——只改生产者会使 UI 把监控视图当作可执行推荐（codex #385 r5）。

#### Scenario: HOLD 工件在决策页被阻断为监控视图

- **WHEN** 决策页加载一个 `rebalance_day: false` 的推荐工件
- **THEN** 页面显示 HOLD 状态与 `next_rebalance_date`，入场决策
  表单不可提交（或等效阻断），列表明确标注为监控视图

#### Scenario: 旧工件向后兼容

- **WHEN** 决策页加载一个不含 `rebalance_day` 字段的历史工件
- **THEN** 渲染行为与现契约一致，不出现 HOLD 阻断

### Requirement: 决策页 SHALL 以 manifest 身份披露 ensemble 工件

决策页 SHALL 识别 meta 携带 `ensemble` 块的推荐工件（PR-A'
ensemble 模式产物）并以其 `manifest_sha256` 披露身份：单模型
sidecar 的 `pkl_sha256` 交叉核对对 ensemble 工件是类别错误，
SHALL NOT 将其误报为"其他模型"或"无法交叉核对"（codex #390
r3）；journal 记录的模型身份 SHALL 采用内容绑定的
`ensemble:<manifest_sha256>` 形式。ensemble 形态的现任 manifest
交叉核对随生产切换（PR-C'）落地。

#### Scenario: ensemble 工件的身份披露

- **WHEN** 决策页加载一个 meta 含 `ensemble` 块（携
  `manifest_sha256`）的推荐工件
- **THEN** 页面以 manifest sha256 披露其 ensemble 来源（专用
  提示，非"其他模型"警告），journal 身份记为
  `ensemble:<manifest_sha256>`

#### Scenario: ensemble 块缺身份哈希时如实警示

- **WHEN** 工件 meta 的 `ensemble` 块缺 `manifest_sha256`
- **THEN** 页面警示无法绑定该工件身份（不伪造、不静默），
  journal 身份回退为诚实的路径级标识
