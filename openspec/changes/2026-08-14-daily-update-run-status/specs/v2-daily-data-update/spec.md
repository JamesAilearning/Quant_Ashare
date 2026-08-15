# Delta for v2-daily-data-update

## ADDED Requirements

### Requirement: 每次运行 SHALL 落盘一份机器可读的终态状态工件

`run_daily_update` SHALL persist a run-status JSON artifact at
`<provider_dir>.daily_update_status.json` — a SIBLING of the provider
dir (so it survives the atomic swap) and NAME-DERIVED (so sibling bundles do
not share one record) — (overridable via the CLI
`--status-path`): written with `state: "running"` at run start, and rewritten
with `state: "finished"`, the run's `exit_code`, the failing stage key
(`failed_stage`, `null` on success) and a human-readable `detail` at every
terminal state OF THE ORCHESTRATOR — including the non-trading-day
calendar-gate no-op (exit 0). CLI-level exits that never enter the orchestrator
(config error exit 2; single-flight conflict exit 17) SHALL NOT write the
artifact: a refused second run must not clobber the record of the legitimate
run holding the lock. The write SHALL be atomic (temp file + rename). A
`--dry-run` SHALL NOT write the artifact. A failure to write the artifact
SHALL be logged as an error and SHALL NOT change the run's exit code — the
artifact is observability, never a canonical input, and no module outside
`src/data_pipeline/daily_update.py` SHALL consume it inside `src/`. Because
the write is an unconditional atomic replace staged through a `<target>.tmp`
sibling (written first, then `os.replace`d over the target), the guard SHALL
validate **both** the final target and that `.tmp` staging sibling: either
one resolving inside the provider dir, the tushare dir, or the swap
machinery's `<provider>.new` / `<provider>.bak` siblings, or aliasing the
delisted registry, the reference cases, or any single-flight lock file,
SHALL be rejected as a config error (exit 2) at config construction —
before any write — so a mistyped
observability path can never clobber a canonical input or an operational
swap path. A name-less `--status-path` (`.`, a filesystem root) SHALL
likewise be rejected at config construction, since the atomic write's
temp-rename requires a file name.

Every record SHALL carry the normalized identity of the provider it
describes (`provider_dir`): two independently scheduled providers may point
the same explicit `--status-path` at one file, their unlocked writes race
through the same staging sibling, and without an identity stamp the reader
cannot even in principle detect the mix-up.

#### Scenario: 成功运行留下 finished/0 记录
- **WHEN** 一次完整运行全部阶段通过并完成 swap
- **THEN** 状态工件为 `state: "finished"`、`exit_code: 0`、
  `failed_stage: null`，且 `started_at`/`finished_at` 齐全

#### Scenario: 失败运行记录失败阶段与退出码
- **WHEN** 任一阶段失败并短路（如 fetch 硬失败 exit 11、validate 失败
  exit 15、swap 失败 exit 16）
- **THEN** 状态工件为 `state: "finished"`、对应该阶段的 `exit_code` 与
  `failed_stage` 阶段键，且不出现后续阶段的记录

#### Scenario: 干跑与日历门 no-op 的写入边界
- **WHEN** `--dry-run` 运行
- **THEN** 状态工件不被创建或修改
- **WHEN** 非交易日日历门 no-op（exit 0）
- **THEN** 状态工件照常记录该次运行（exit 0、无 failed_stage），操作人
  能看到「任务按时跑了且正确地没做事」

#### Scenario: 状态写失败不反转运行结果
- **WHEN** 状态工件写入本身失败（如目标目录不可写）
- **THEN** 运行以原有退出码结束并记录 ERROR 日志——可观测性故障绝不
  改变数据更新的成败

#### Scenario: 状态路径覆盖 canonical 输入被拒绝
- **WHEN** `--status-path` 解析后落在 provider 目录、tushare 原始数据
  目录、`<provider>.new` / `<provider>.bak` 交换暂存/回滚目录之内，
  或与 delisted registry / reference cases 是同一路径，或根本没有
  文件名（`.`、文件系统根）
- **THEN** 配置构造即以 ValueError 拒绝（CLI 映射为配置错误 exit 2），
  任何状态写入都不会发生——打错的可观测性路径绝不能毁掉 canonical
  数据或交换机械的操作路径
