# Delta for v2-operator-ui

## ADDED Requirements

### Requirement: 数据检视页 SHALL 展示上次数据更新的运行状态

The bundle inspector page SHALL render a "上次数据更新" section (before the
fetch-integrity stamp section) sourced from the run-status artifact derived
from the inspected provider path (`<provider_dir>.parent/daily_update_status.json`).
The section SHALL distinguish: running (state=running), finished-ok
(exit_code 0), finished-failed (exit_code + failed_stage prominent), missing
artifact (an explicit "从未记录" informational state, not an error), and
malformed artifact (a prominent error — never a silent default). The section
SHALL remain strictly read-only, and the page source SHALL NOT name the
orchestrator or swap machinery (`daily_update` / `bundle_swap`) — the
governance source scan keeps passing.

#### Scenario: 失败一目了然
- **WHEN** 状态工件记录 `exit_code: 15`、`failed_stage: "validate"`
- **THEN** 页面以错误态展示退出码与失败阶段，操作人无需翻日志即知
  昨晚更新死在校验

#### Scenario: 缺失与损坏不静默
- **WHEN** 状态工件不存在（新机器/首跑前）
- **THEN** 页面显示「从未记录」的提示态而非报错
- **WHEN** 状态工件存在但不是合法 JSON / 形状违约
- **THEN** 页面显示醒目的损坏错误，绝不用默认值顶替
