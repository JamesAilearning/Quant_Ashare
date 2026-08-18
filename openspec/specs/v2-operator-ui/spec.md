# v2-operator-ui Specification

## Purpose
TBD - created by archiving change 2026-06-10-thin-production-inspector. Update Purpose after archive.
## Requirements
### Requirement: The UI SHALL provide a read-only inspector of the production bundle

The operator UI SHALL provide a 数据检视 page that only INSPECTS the production
qlib bundle and SHALL NOT build, ingest, or mutate any data. The page SHALL
surface: the bundle's fetch-integrity stamp (P3-4c) — clean, holey (with the
recorded holes), missing, or corrupt, each with its operator consequence; the
bundle-health summary; and an on-demand, read-only run of the PIT validator
rendered as a per-check report. The page copy SHALL state explicitly that it
inspects production data and that bundles are produced by the data pipeline,
not the UI. Read-only is machine-enforced: the page source SHALL contain no
write-side filesystem API and SHALL NOT import builder / fetcher /
orchestrator machinery.

#### Scenario: a holey bundle is surfaced with its holes
- **WHEN** the inspected bundle's integrity stamp says built-from-holey-fetch
- **THEN** the page shows the holes and states the recommend boundary refuses
  the bundle by default

#### Scenario: an unstamped or corrupt stamp is surfaced loudly
- **WHEN** the bundle has no integrity stamp, or the stamp is unreadable
- **THEN** the page says completeness cannot be confirmed (or the stamp is
  corrupt) rather than implying the bundle is clean

#### Scenario: the validator runs read-only on demand
- **WHEN** the operator triggers validation
- **THEN** the 06 PIT checks run against the production bundle and render as a
  report, and nothing on disk is written

#### Scenario: the read-only contract is machine-checked
- **WHEN** the governance suite runs
- **THEN** a source-level test fails on any write-side filesystem API or any
  builder / fetcher / orchestrator import in the page

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
the read-side module, not inline page logic.

The reader SHALL require the record's `provider_dir` identity and the page
SHALL refuse to present a record naming a DIFFERENT provider as this
bundle's status (a prominent error explaining the shared `--status-path`
cause) — never render it as if it were this provider's. The page's
transitive `web.operator_ui` import closure SHALL NOT import the
orchestrator or swap machinery, and SHALL NOT import process-spawning
modules (the single audited exemption is the PIT-validation runner, whose
argv is pinned to the 06 validator). The section SHALL remain strictly read-only, and the page SHALL NOT
**import or invoke** the orchestrator or swap machinery (`daily_update` /
`bundle_swap`) — the coupling is what the governance scan forbids. Prose
references in operator-facing text and comments (e.g. "bundles are made by
daily_update", the derived artifact filename) are permitted: a requirement
banning the mere string would contradict the page as shipped — it names the
pipeline in its docstring, a caption, and the status-path help — while the
scan it cites checks import lines only, so the spec would demand something
nothing enforces and nothing satisfies (codex #434 r11).

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

