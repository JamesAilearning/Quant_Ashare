# v2-daily-data-update Specification

## Purpose
TBD - created by archiving change 2026-06-10-daily-update-orchestrator. Update Purpose after archive.
## Requirements
### Requirement: A single entry point SHALL orchestrate the daily data update fail-loud

`scripts/daily_update.py` SHALL run the full update as ordered stages — fetch
(with `--refresh-current`) → active-stocks snapshot check → full rebuild into
`<provider>.new` (registry → bins → index membership → universe; bins BEFORE
the instruments writers because its staging-promote replaces the output dir) →
validation of the STAGED bundle → atomic swap. Every stage failure SHALL
short-circuit the remaining stages and exit with a code that identifies the
failing stage. Paths SHALL flow into every pipeline script as explicit CLI
argv (no environment-variable coupling). `--allow-holey-fetch` SHALL pass
through to the build gate only and SHALL NOT touch the recommend-side
override. `--dry-run` SHALL print each stage's argv and the bundle state and
SHALL execute and mutate nothing.

#### Scenario: stages run in order and the bundle is swapped
- **WHEN** every stage succeeds
- **THEN** the stages ran fetch → registry → bins → membership → universe →
  validate in that order, the staged bundle is promoted live, and the previous
  bundle is kept as the rollback backup

#### Scenario: a failing stage short-circuits the rest
- **WHEN** the fetch fails hard (or any rebuild stage fails)
- **THEN** no later stage runs and the exit code identifies the failing stage

#### Scenario: fetch holes stop the run unless overridden
- **WHEN** the fetch completes with holes (exit 3) and `--allow-holey-fetch`
  was not given
- **THEN** the run stops before rebuilding (the build gate would refuse the
  dump anyway); with the override it continues and the bundle is stamped
  built-from-holey-fetch by the build layer

#### Scenario: the snapshot stage proves the refresh landed
- **WHEN** after the fetch the embedded snapshot_date of active_stocks.parquet
  does not equal the run date (or the stamp is missing/unreadable)
- **THEN** the run refuses (exit 13) unless `--allow-holey-fetch` sanctioned
  partial data, in which case it warns and continues

#### Scenario: dry-run executes nothing
- **WHEN** `--dry-run` is given (even with a repairable crash state on disk)
- **THEN** the plan and the bundle state are printed, no stage runs, and
  nothing on disk changes

### Requirement: The bundle swap SHALL be atomic, validated-only, and crash-repairable

The live provider bundle SHALL only ever be replaced by a two-stage rename
swap — stage 1 `provider → provider.bak`, stage 2 `provider.new → provider` —
executed ONLY after the staged bundle passed validation. A failed validation
SHALL never reach the swap and SHALL leave the live bundle untouched. The
backup SHALL be kept as instant rollback until the next swap. At startup the
orchestrator SHALL detect and resolve every reachable crash state: a swap
interrupted between the two renames (backup + staged present, live missing —
stage 1 having run proves validation passed) SHALL be COMPLETED; a
backup-only state SHALL be RESTORED; a stale staged bundle (which cannot be
proven validated) SHALL be REMOVED loudly and never auto-promoted. The swap's
atomicity bound is CRASH-atomicity (no observer ever sees a half-written
bundle; every interrupted state is repaired), NOT reader-concurrency: between
the two renames the live path briefly does not exist, so a concurrent reader
errs fail-loud rather than reading torn data — the daily update is meant to
run when nothing reads the bundle (scheduling, Phase 4).

#### Scenario: crash after build, before swap
- **WHEN** a prior run died leaving the live bundle plus a stale `.new`
- **THEN** the next startup removes the stale `.new` loudly and the live
  bundle is byte-identical untouched

#### Scenario: crash between the two renames
- **WHEN** a prior run died after stage 1 (backup exists, live missing,
  staged present)
- **THEN** the next startup completes stage 2 — the validated staged bundle
  goes live and the backup is preserved

#### Scenario: crash after the swap completed
- **WHEN** both renames finished before the crash
- **THEN** the next startup recognizes the healthy post-swap state and touches
  nothing

#### Scenario: validation failure never swaps
- **WHEN** validation of the staged bundle FAILS (validator exit >= 2 — a check
  did not pass)
- **THEN** the swap never runs, the live bundle is untouched, and the staged
  bundle is left in place for inspection

#### Scenario: warnings-only validation is a pass
- **WHEN** the validator returns its warnings-only code (exit 1 — every check
  passed; routine when reference cases are present)
- **THEN** the swap proceeds, with the warnings surfaced loudly in the log —
  a valid bundle is never wedged behind a benign warning

### Requirement: The daily update SHALL no-op cleanly on a non-trading day

`run_daily_update` SHALL return exit 0 (success) on a default run whose date is NOT a
trading day (no explicit end-date requested), WITHOUT running any fetch/build/swap stage
— so a scheduled daily run does not run the full pipeline (or churn the bundle with a
no-data swap) on a closed day.

The gate SHALL be placed AFTER the dry-run preview (a dry-run still prints the plan) and
AFTER the Stage 0 startup crash-repair, so that an interrupted prior swap (a crash that
left the live provider missing with a `.bak`/`.new` pair) is ALWAYS completed — even on
a closed day — rather than leaving readers without a live bundle until the next trading
day. The gate SHALL NOT fire for an explicit end-date run: an operator-supplied
`--end-date` is a deliberate backfill / catch-up (e.g. recovering a missed Friday update
on Saturday) and SHALL run, never silently no-op.

The no-op SHALL fire only when a readable qlib bundle skeleton exists after the Stage 0
repair — `calendars/day.txt` AND `instruments/all.txt` AND `features/` all present (the
same cheap structural set `pit_validator._sanity_check_provider` uses to define a readable
provider), NOT a bare path, a merely non-empty directory, or the calendar spine alone. Its
premise is "the bundle is already current, skip the redundant refresh", which holds only
if a usable bundle is present. On a fresh machine, after a first-ever build crashed leaving
only a `.new` that repair cleared, when the provider path is empty / a stray file / a
garbage layout (an operator `mkdir`, an antivirus or cloud-sync tool that deleted a
corrupted bundle's files but left the folder), or a PARTIAL copy that kept `calendars/
day.txt` but lost `instruments/all.txt` / `features/`, no usable live bundle exists; the
gate SHALL NOT no-op there (that would report success with no readable bundle) — it falls
through to the normal pipeline so a bundle is bootstrapped from history, or the run fails
loud.

The trading-day determination SHALL be offline and deterministic (a weekend check), so
the orchestrator hot path and the test suite take no network. A-share weekday holidays
are NOT skipped by this gate — they fall through to the normal run, whose fetch/freshness
gates already no-op gracefully on a day with no new bar — so a weekday holiday is
handled, never wrongly skipped.

#### Scenario: a weekend run is a clean no-op
- **WHEN** `run_daily_update` runs with a default run date on a Saturday or Sunday
- **THEN** it returns exit 0 and runs NO fetch/build/swap stage

#### Scenario: a crashed swap is still repaired on a non-trading day
- **WHEN** a prior swap crashed mid-rename (live provider missing, `.bak` + `.new` present) and `run_daily_update` runs on a Saturday
- **THEN** the Stage 0 repair completes the interrupted swap (live provider restored) BEFORE the gate no-ops, so readers are not stranded over the weekend

#### Scenario: a weekend run with no usable live bundle bootstraps instead of no-op'ing
- **WHEN** `run_daily_update` runs on a Saturday with no readable qlib bundle — absent (fresh machine, or repair just cleared a stale `.new`), a present path that is an empty / stray-file / garbage directory, OR a partial copy missing `instruments/all.txt` / `features/`
- **THEN** the gate does NOT no-op; the full pipeline runs so a bundle is bootstrapped to a live swap (never a green exit with no bundle)

#### Scenario: an explicit end-date backfill overrides the gate
- **WHEN** `run_daily_update` runs on a Saturday with an explicit `--end-date`
- **THEN** the gate does not fire and the full pipeline runs (a deliberate catch-up)

#### Scenario: a trading-day run proceeds normally
- **WHEN** `run_daily_update` runs with a run date on a weekday
- **THEN** the gate passes and the full pipeline runs as before

#### Scenario: the dry-run preview precedes the gate
- **WHEN** `run_daily_update` runs with `--dry-run` on a non-trading day
- **THEN** the plan is still previewed and the gate does not pre-empt the preview

### Requirement: The daily update SHALL refuse a concurrent run sharing any mutable input

The `daily_update` CLI SHALL acquire a process-exclusive single-flight lock on EVERY
mutable input a run touches — the provider dir AND the shared raw inputs (the tushare
dump, the delisted registry) — BEFORE any mutation, so two runs that share ANY of them
cannot overlap. The swap is crash-atomic but NOT run-concurrent (overlapping runs would
race the `provider` / `.bak` / `.new` triplet), and the fetch / registry stages write
fixed-name temp files under the shared raw paths (overlapping runs would clobber them even
with different providers).

Each lock SHALL be an OS advisory lock (`fcntl.flock` / `msvcrt.locking`), NOT a pidfile,
so the kernel releases it when the holder exits — including on a crash or kill — leaving
no stale lock to reclaim and no PID-reuse / corrupt-lock wedge. The locks SHALL be taken
in a canonical order so exactly one of two contending runs wins (the other refuses
cleanly; the non-blocking locks cannot deadlock). A run that cannot take every lock SHALL
fail fast with a distinct exit code (17, already-running), release any lock it took, and
run NO stage. A `--dry-run` mutates nothing and SHALL be exempt.

Each lock file SHALL be a sibling of its resource (not a child), since the swap renames
the provider dir wholesale; and runs that share NO mutable input SHALL NOT contend.

#### Scenario: a run sharing a mutable input with a live run is refused
- **WHEN** the `daily_update` CLI starts while another run holds a lock for any shared input (same provider, tushare dump, or registry)
- **THEN** it exits 17 (already-running) and runs no fetch/build/swap stage

#### Scenario: distinct providers sharing a raw input are serialized
- **WHEN** two runs use DIFFERENT `--provider-dir` but the SAME `--tushare-dir` (or `--delisted-registry`)
- **THEN** the second is refused — they would otherwise clobber the shared raw temp files

#### Scenario: the lock is released when the holder exits
- **WHEN** a run that held the locks exits (normally or by crash) and a later run starts
- **THEN** the later run acquires the locks and proceeds — no manual clearing is needed

#### Scenario: runs that share no mutable input do not contend
- **WHEN** two `daily_update` runs target fully disjoint provider / tushare / registry paths
- **THEN** each takes its own locks and runs

#### Scenario: a dry-run is exempt from the lock
- **WHEN** the `daily_update` CLI runs with `--dry-run` while a lock is held
- **THEN** it previews the plan and is not blocked

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
the write is an unconditional atomic replace staged through a UNIQUE
per-write sibling (`<target>.<pid>.<uuid>.tmp` — unique so that two
providers sharing an explicit `--status-path` cannot truncate each other's
staging mid-replace and publish an empty artifact; the final `os.replace`
is the only shared step and is atomic; a failed write SHALL remove its
private staging file), the guard SHALL validate **both** the final target
and the HISTORICAL fixed `<target>.tmp` staging sibling (no longer written,
kept out of the config space as a forbidden alias): either
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
the same explicit `--status-path` at one file. The unique per-write staging
above removes the WRITE race, but the file still holds whichever record was
published last — without an identity stamp the reader cannot even in
principle detect that it is looking at the other provider's run.

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

