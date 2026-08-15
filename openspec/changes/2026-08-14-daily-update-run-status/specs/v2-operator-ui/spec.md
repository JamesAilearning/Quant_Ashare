# Delta for v2-operator-ui

## ADDED Requirements

### Requirement: 数据检视页 SHALL 展示上次数据更新的运行状态

The bundle inspector page SHALL render a "上次数据更新" section (before the
fetch-integrity stamp section) sourced from the run-status artifact derived
from the inspected provider path
(`<provider_dir>.daily_update_status.json` — sibling of, and unique to,
that provider). Because the updater CLI advertises a `--status-path`
override, the page SHALL let the operator override the artifact location
(defaulting to the derived path) — a deployment that schedules the updater
with a custom `--status-path` must not be shown 从未记录 (or a stale older
record) while last night's run sits in the custom file; the missing-artifact
wording SHALL point at this override as a cause to check.
The section SHALL distinguish: running (state=running), finished-ok
(exit_code 0), finished-failed (exit_code + failed_stage prominent), missing
artifact (an explicit "从未记录" informational state, not an error), and
malformed artifact (a prominent error — never a silent default). The reader
SHALL validate the COMPLETE state-specific schema before believing a record:
`schema_version` must equal the supported version (an absent or unsupported
version is never interpreted with v1 semantics), `run_date`/`started_at`
must be non-empty on every record, and a finished record must carry a
non-empty `finished_at` plus the `failed_stage` and `detail` keys the writer
always emits (`failed_stage` null or a non-empty stage key, `detail` a
string), honoring the success/failure invariant (exit_code 0 ⇔ failed_stage
null). An incomplete or invariant-breaking record renders as corrupt, never
as a green success.

A persisted `state=running` record SHALL NOT, by itself, be rendered as an
actively running update: a killed / power-lost / crashed run leaves it on
disk until the next invocation overwrites it. The page SHALL render
正在运行 only when the record's age is computable and within a staleness
threshold **starting at zero** — a NEGATIVE age (a `started_at` in the
future: clock skew, or a fabricated timestamp) SHALL be treated as
unverifiable, never as fresh. Past the threshold the page SHALL say the run
may have been interrupted and that it cannot tell; when the age cannot be
computed at all (missing / unparseable / timezone-naive `started_at`) the
page SHALL use distinct wording that asserts **no** age — claiming
"已超过 N 小时" about an age nobody computed is the same defect as claiming
the run is active. The classification SHALL be a pure, tested function of
the read-side module, not inline page logic. The section SHALL remain strictly read-only, and the page source
SHALL NOT name the orchestrator or swap machinery (`daily_update` /
`bundle_swap`) — the governance source scan keeps passing.

#### Scenario: 失败一目了然
- **WHEN** 状态工件记录 `exit_code: 15`、`failed_stage: "validate"`
- **THEN** 页面以错误态展示退出码与失败阶段，操作人无需翻日志即知
  昨晚更新死在校验

#### Scenario: 缺失与损坏不静默
- **WHEN** 状态工件不存在（新机器/首跑前）
- **THEN** 页面显示「从未记录」的提示态而非报错
- **WHEN** 状态工件存在但不是合法 JSON / 形状违约
- **THEN** 页面显示醒目的损坏错误，绝不用默认值顶替

#### Scenario: 截断或未知版本的记录不渲染成成功
- **WHEN** 状态工件是 `{"state":"finished","exit_code":0}` 这类缺字段
  记录（缺 `finished_at`/`failed_stage`/`detail` 任一），或
  `schema_version` 缺失/不受支持，或 `exit_code` 与 `failed_stage`
  违反成功/失败不变式
- **THEN** 页面显示醒目的损坏错误并指明缺失字段/版本/不变式问题——
  绝不把截断或未来的记录按 v1 语义渲染成绿色成功
