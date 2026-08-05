# v2-daily-stock-recommendation Specification

## Purpose
TBD - created by archiving change add-daily-stock-recommendation. Update Purpose after archive.
## Requirements
### Requirement: Daily recommendation SHALL construct the as-of cross-section from data on or before the decision date

The daily recommendation path SHALL, for a decision date `T`, build the
model's input feature cross-section using only market data dated `≤ T`.
The Alpha158 handler SHALL be constructed with `end_time = T` and its
inference processors SHALL be fit on the training window
(`fit_start_time = fit_start`, `fit_end_time = fit_end`), so that no
statistic and no feature value depends on any bar dated `> T`. The
forward-looking training label SHALL NOT be computed or consumed during
inference.

#### Scenario: feature frame for date T contains no future rows
- **WHEN** `recommend` is invoked for as-of date `T` against a PIT
  bundle whose calendar extends beyond `T`
- **THEN** the prepared feature frame's maximum datetime equals `T`
- **AND** no row dated later than `T` is present in the frame passed to
  `model.predict`

#### Scenario: normalization statistics do not use the decision date
- **WHEN** the Alpha158 handler is built for inference at as-of date `T`
- **THEN** its infer-processor fit window ends at `fit_end` (the
  training fit end), not at `T`
- **AND** the label column is never requested (only `col_set="feature"`)

### Requirement: Daily recommendation SHALL resolve the as-of date to a real trading day

When no as-of date is supplied, the path SHALL default to the LATEST
trading day in the PIT calendar that still has a following session (i.e.
the second-to-last day when the calendar ends at the data cutoff), so a
next-session (`T+1`) entry exists and the no-argument path is usable. The
last calendar day SHALL NOT be a default decision day because no `T+1`
session exists for it in the bundle. When an as-of date is supplied, it
SHALL be a trading day on or before the calendar's last day; a
non-trading or out-of-range date — or an explicit last-day with no `T+1`
— SHALL fail with an explicit error rather than silently snapping or
producing an empty list.

#### Scenario: default as-of is the latest day with a following session
- **WHEN** `recommend` is invoked with no as-of date
- **THEN** the result's `as_of_date` equals the latest calendar trading
  day that has a following session
- **AND** `entry_date` equals that following session

#### Scenario: out-of-range as-of date is rejected
- **WHEN** `recommend` is invoked with an as-of date after the PIT
  calendar's last trading day
- **THEN** an explicit error is raised and no list is produced

### Requirement: Daily recommendation SHALL exclude untradable names from the buy list

The path SHALL compute the `T`-day microstructure mask (suspension:
`$volume <= 0` or `$close` NaN; one-price-lock: `$volume > 0` and
`$high == $low`) for the candidate universe and SHALL exclude masked
`(T, instrument)` candidates from the Top-K buy list. The full scored
frame, including masked names with an `unavailable_reason`, SHALL be written for
audit so the exclusion is inspectable, not silent.

#### Scenario: a stock suspended on T is not recommended
- **WHEN** instrument `SH600000` has `$volume == 0` on the as-of date
- **THEN** `SH600000` is absent from the Top-K buy list
- **AND** it appears in the audit frame with
  `unavailable_reason = "suspended"`

#### Scenario: a one-price-locked stock on T is not recommended
- **WHEN** instrument `SH600000` has `$volume > 0` and `$high == $low`
  on the as-of date
- **THEN** `SH600000` is absent from the Top-K buy list
- **AND** it appears in the audit frame with
  `unavailable_reason = "one_price_lock"`

### Requirement: Daily recommendation SHALL emit a ranked, dated, persisted buy list

The path SHALL rank tradable candidates by predicted score descending,
truncate to the configured `topk` (default 50), and emit a list whose
rows carry `as_of_date, entry_date, rank, stock_code, stock_name,
predicted_score, tradable_flag, unavailable_reason`. Ranks SHALL be
contiguous `1..N` with `N ≤ topk`. The list SHALL be persisted as both
`daily_recommendation_<date>.csv` and `.json`, and printed to the
terminal. The two time points — `as_of_date` (data cutoff T) and
`entry_date` (suggested entry T+1) — SHALL both appear.

**Cadence-aware entry semantics（本次修订，codex #385 r3/r4——
消除 HOLD 日双重语义且不触碰 as-of 契约）**：在节奏化生产配置
（cadence ≠ 1）下，工件 SHALL 携带 `rebalance_day: true|false`
与 `next_rebalance_date`（下一再平衡日，追加字段）；
`rebalance_day: true` 时本列表是可执行的 T+1 入场清单（上文语义
原样）；`rebalance_day: false` 时工件仍按同 schema 持久化（行
内容、排名、双时间点含 `entry_date` = 下一交易日**全部照常**——
`entry_date` 的 as-of 解析契约不因节奏而变）但 SHALL NOT 构成
入场指令——输出 SHALL 附醒目 HOLD 提示，入场参考由
`next_rebalance_date` 字段承载而非改写 `entry_date` 语义。日频
配置（cadence = 1，含现行为）下每日皆再平衡日，语义与本
requirement 原文逐字一致、路径不变。

#### Scenario: output is ranked and bounded
- **WHEN** `recommend` produces a result with `topk = 50`
- **THEN** the buy list has at most 50 rows
- **AND** rows are ordered by `predicted_score` descending with
  contiguous ranks `1..N`
- **AND** a `daily_recommendation_<date>.csv` and `.json` are written
  carrying both `as_of_date` and `entry_date`

#### Scenario: HOLD 日工件不构成入场指令且 entry_date 契约不变
- **WHEN** 节奏化生产配置下在非再平衡日（例：周一再平衡后的
  周二）运行 recommend
- **THEN** 工件照常持久化，`entry_date` 仍等于下一交易日（as-of
  解析契约原样），`rebalance_day: false` + HOLD 提示标明列表为
  监控视图而非入场指令，`next_rebalance_date` 携带下一再平衡日

### Requirement: Daily recommendation SHALL use the Alpha158 + LGB signal and align with its execution horizon

The path SHALL source predictions from a model trained with the
Alpha158 feature handler (not GP-mined factors in this version). The
recommendation SHALL be documented as a next-session (`T+1`) entry
signal, consistent with the Alpha158 default label
`Ref($close, -2) / Ref($close, -1) - 1` (T+1→T+2 return) used in
training.

#### Scenario: model artifact is loaded and scored without retraining
- **WHEN** `recommend` is given a path to a previously trained model
  artifact
- **THEN** the model is loaded from that artifact and used to score the
  as-of cross-section without retraining

### Requirement: Daily recommendation SHALL exclude current ST/*ST names from the buy list before the Top-K slice

The path SHALL determine, from the current name snapshot, which candidate
names carry an A-share ST-family risk-warning marker (`ST`, `*ST`, `SST`,
`S*ST`, and resumption-day `NST`; NOT bare `S`, `N`/`C`, `XD`/`XR`/`DR`, or
Latin company names) and SHALL remove those names from the candidate pool
**before** truncating to `topk`, so the buy list holds `topk` tradable,
non-ST picks rather than `topk` minus the ST hits. Excluded ST names SHALL
remain in the full scored audit frame with `unavailable_reason = "st"`, and
the result SHALL report the count as `n_st_excluded`. When a name is both
microstructure-masked and ST, the microstructure reason SHALL take
precedence in the audit label.

#### Scenario: an ST stock is not recommended and is labelled
- **WHEN** a candidate whose current name is `*ST金亚` scores within the
  Top-K on the as-of date
- **THEN** it is absent from the buy list
- **AND** it appears in the audit frame with `unavailable_reason = "st"`
- **AND** the result's `n_st_excluded` counts it

#### Scenario: the Top-K is filled from the non-ST pool
- **WHEN** the scored pool interleaves ST and non-ST names by score and
  `topk = K`
- **THEN** the buy list contains the `K` highest-scoring non-ST names
- **AND** no ST name appears in the buy list

### Requirement: Daily recommendation SHALL fail loud when the current-ST source is missing, stale, or malformed

Because excluding ST requires the current name snapshot, the path SHALL treat
that snapshot as REQUIRED and SHALL raise an explicit error and emit no list
(rather than silently producing a list that could include ST names) when any of
the following holds:

- `name_source_parquet` is unset or the file is absent;
- the snapshot is STALE — staleness is judged by the snapshot's EMBEDDED
  `snapshot_date` column (stamped by the fetcher at fetch time), NOT by file
  mtime, which sync / copy tools rewrite so a stale snapshot can look fresh; a
  snapshot whose embedded date lags the as-of date by more than
  `st_snapshot_max_age_days` is refused;
- the snapshot lacks the embedded `snapshot_date` column (written before the
  stamp existed) — it fails loud with a re-fetch instruction, never a silent
  mtime fallback;
- the snapshot is unreadable, is missing the required `ts_code` or `name`
  column, or is empty;
- the embedded snapshot_date is malformed (empty / all-null, multiple distinct
  values, or non-YYYYMMDD).

A snapshot whose embedded date is newer than the as-of date SHALL NOT be treated
as stale.

#### Scenario: a missing current-ST source is rejected
- **WHEN** `recommend` is invoked with no `name_source_parquet` (or a path
  that does not exist)
- **THEN** an explicit error is raised and no list is produced

#### Scenario: a stale snapshot is rejected by its embedded date, not mtime
- **WHEN** the snapshot's embedded snapshot_date lags the as-of date beyond
  `st_snapshot_max_age_days`, however fresh the file's mtime is
- **THEN** `recommend` refuses (DailyRecommendationError) and no list is produced

#### Scenario: an old-format snapshot without the embedded column fails loud
- **WHEN** the snapshot has no embedded snapshot_date column
- **THEN** `recommend` refuses with a re-fetch instruction rather than falling
  back to mtime

#### Scenario: a malformed current-ST snapshot is rejected
- **WHEN** the snapshot is present and fresh but is unreadable, is missing
  the `ts_code` or `name` column, or has zero rows
- **THEN** an explicit error is raised and no list is produced
- **AND** the path does NOT fall back to an empty name map that would
  silently disable ST filtering

#### Scenario: conflicting embedded dates fail loud
- **WHEN** the snapshot carries more than one distinct snapshot_date value
- **THEN** `recommend` refuses (corrupt / hand-merged file)

#### Scenario: a fresh embedded date passes
- **WHEN** the embedded snapshot_date is within tolerance of the as-of date
- **THEN** the guard passes and returns the snapshot date

### Requirement: The walk-forward backtest SHALL exclude PIT-historical ST/*ST names from the selection set before TopkDropout

When a namechange source is configured, the walk-forward backtest SHALL drop,
from the (signal-lag-shifted) prediction set passed to `TopkDropoutStrategy`,
every `(execution_date, instrument)` whose instrument was ST/*ST on that
execution date. ST status SHALL be reconstructed point-in-time as the name in
effect on the date — the namechange row with the greatest `start_date <= date`
(`end_date` SHALL NOT be used) — and a row whose `start_date` is after the date
SHALL NOT be consulted (no look-ahead). The exclusion SHALL be selection-time
only: ST names SHALL remain in the model's training panel. When the configured
namechange source is missing, unreadable, malformed, or does not cover the
evaluation window, the backtest SHALL fail loud rather than run ST-unmasked. A
per-run ST mask audit listing the dropped `(date, instrument, ts_code, name)`
rows SHALL be written for operator review.

#### Scenario: a name ST on the execution date is dropped
- **WHEN** instrument `X` was ST/*ST (per its as-of namechange name) on
  execution date `D` and a namechange source is configured
- **THEN** the `(D, X)` candidate is absent from the set passed to
  `TopkDropoutStrategy`
- **AND** it appears in the ST mask audit with its as-of name

#### Scenario: a name that became ST only after D is not dropped for D
- **WHEN** instrument `X`'s earliest ST namechange has `start_date` after `D`
- **THEN** `(D, X)` is NOT dropped (the status reflects `D`, not a later
  relabel)

#### Scenario: training is unaffected by the selection mask
- **WHEN** the ST mask drops names from the selection set
- **THEN** the model for that fold was still trained on a panel that included
  those names (the mask runs on predictions, never on the training data)

#### Scenario: missing or uncovered namechange fails loud
- **WHEN** the configured namechange source is absent, malformed, or its latest
  record predates the evaluation window
- **THEN** the backtest raises and produces no metrics (no ST-unmasked
  fallback)

### Requirement: Daily recommendation SHALL refuse to emit a list when the price/feature bundle is stale

`recommend` SHALL verify the bundle's freshness against an EXTERNAL reference
date and refuse to emit a list when the bundle is stale — because it resolves
the as-of date from the qlib bundle's own calendar, it cannot otherwise detect
its own staleness. It SHALL compare the bundle's last trading day to a reference
"today" (the system date in production, injectable for tests and intentional
historical runs) and, if the lag exceeds the configured `bundle_max_age_days`
(calendar days), SHALL raise an explicit error and emit no list rather than
scoring on stale prices. The tolerance SHALL be generous enough that a normal
pre-holiday gap (no new data during a multi-day market holiday) does not trip
it. A bundle whose last trading day is on or after the reference today SHALL NOT
be treated as stale.

#### Scenario: a stale bundle is rejected
- **WHEN** the bundle's last trading day lags the reference today by more than
  `bundle_max_age_days`
- **THEN** an explicit error is raised and no list is produced
- **AND** the error names the bundle's last day and the remedy (update the
  bundle before recommending)

#### Scenario: a fresh bundle (including a normal holiday gap) is accepted
- **WHEN** the bundle's last trading day lags the reference today by no more
  than `bundle_max_age_days` — including a multi-day market-holiday gap during
  which no new data is expected
- **THEN** the freshness guard does not raise and the list is produced

#### Scenario: the reference today is injectable and deterministic
- **WHEN** a reference today is supplied to `recommend`
- **THEN** the freshness comparison uses that value rather than the wall-clock
  date, so the guard is deterministic for tests and lets an operator override
  it for an intentional historical run

### Requirement: Recommendation SHALL refuse a bundle built from a holey fetch

`recommend` SHALL refuse to emit a buy list from a price/feature bundle that was
built from a HOLEY tushare fetch, or that lacks a fetch-integrity stamp, unless the
operator explicitly opts in. Right after the staleness guard, it SHALL read the
bundle's `_fetch_integrity.json` stamp (written by the qlib bin builder) from the
SAME normalized `provider_uri` qlib initialized against (so an `~`-prefixed or
whitespaced URI is not read from a non-existent literal path): a stamp marked
`built_from_holey_fetch`, OR a MISSING stamp (completeness cannot be confirmed —
e.g. a bundle built before this contract existed), SHALL raise
`DailyRecommendationError` rather than rank a list on survivorship-incomplete data,
unless `allow_holey_recommend` (`--allow-holey-recommend`) is set. This decision
SHALL be INDEPENDENT of the build-side `--allow-holey-fetch`: the stamp carries the
FACT that the fetch was holey, never the authorization to trade on it, so building
a partial bundle SHALL NOT by itself permit recommending from it. A clean stamp
SHALL pass silently. A CORRUPT stamp — malformed / unknown-schema / wrong-typed,
or INTERNALLY INCONSISTENT (marked clean yet listing holes) — SHALL fail loud
REGARDLESS of `allow_holey_recommend`: the override accepts a holey or MISSING
stamp (known states), not an unreadable or self-contradictory one; the stamp SHALL
be read (and a corrupt one surfaced) BEFORE the override is honoured.

#### Scenario: a holey-stamped bundle refuses recommendation
- **WHEN** the bundle's stamp is `built_from_holey_fetch = true` and
  `allow_holey_recommend` is not set
- **THEN** `recommend` raises rather than emitting a list

#### Scenario: an unstamped bundle refuses recommendation
- **WHEN** the bundle has no fetch-integrity stamp and `allow_holey_recommend` is
  not set
- **THEN** `recommend` raises (completeness cannot be confirmed)

#### Scenario: a clean bundle recommends normally
- **WHEN** the bundle's stamp is `built_from_holey_fetch = false`
- **THEN** the gate passes silently and recommendation proceeds

#### Scenario: the override permits an intentional holey run
- **WHEN** `allow_holey_recommend` is set
- **THEN** the gate passes regardless of a holey or missing stamp

#### Scenario: a corrupt stamp fails loud even under the override
- **WHEN** the bundle's stamp exists but is corrupt / unknown-schema and
  `allow_holey_recommend` is set
- **THEN** `recommend` still raises — the override accepts incompleteness (holey /
  missing), not an unreadable stamp; corruption is surfaced before the override

#### Scenario: red line — the build override does not sanction recommendation
- **WHEN** a bundle was built under the build-side `--allow-holey-fetch` (so it is
  stamped `built_from_holey_fetch = true`) and recommendation runs WITHOUT
  `--allow-holey-recommend`
- **THEN** `recommend` still refuses — build-allow never cascades into
  recommend-allow; each boundary opts in on its own

### Requirement: Recommendation SHALL refuse an ST snapshot inconsistent with the bundle

`recommend` SHALL check that the ST snapshot and the price bundle come from the
same update cycle: an embedded `snapshot_date` lagging the bundle calendar's
last trading day by more than `bundle_max_age_days` SHALL raise
`DailyRecommendationError` — the ST/name view would predate the prices being
ranked (e.g. the bundle was rebuilt but stock_basic was never re-fetched). A
snapshot NEWER than the bundle tail SHALL pass (snapshots refresh more often
than bundles).

#### Scenario: a snapshot lagging the bundle tail refuses
- **WHEN** the embedded snapshot_date lags the bundle's last trading day by more
  than `bundle_max_age_days`
- **THEN** `recommend` raises rather than ranking on a mismatched pair

#### Scenario: a same-cycle snapshot passes
- **WHEN** the embedded snapshot_date is within `bundle_max_age_days` of the
  bundle tail (or newer than it)
- **THEN** the consistency check passes silently

### Requirement: The recommendation artifact SHALL carry its own generation context (schema v2)

`write_outputs` SHALL serialize a top-level `meta` block into
`daily_recommendation_{as_of}.json` together with `artifact_schema_version: 2`.
The block SHALL carry: `generated_at` (Asia/Shanghai ISO8601 with explicit
offset), `model_path`, `model_pkl_sha256` (SHA-256 of the loaded model pickle),
`fit_start_for_inference` / `fit_end_for_inference` (the RESOLVED window the run
actually used), `provider_uri`, `bundle_tag` (the `_fetch_integrity` identity
compact tag, or `null` when the bundle carries no identity stamp), `instruments`
and `topk`. The meta SHALL be assembled inside `recommend()` (the single place
that knows the resolved window, model and bundle) and carried on
`DailyRecommendationResult.run_meta` as a REQUIRED field — no default value, so
every constructor (including tests) is forced to supply it rather than silently
omitting context. The buy-list CSV and the scored-audit CSV are unchanged.

#### Scenario: a fresh run writes a self-describing artifact
- **WHEN** `recommend()` completes and `write_outputs` persists the JSON
- **THEN** the JSON contains `artifact_schema_version: 2` and a `meta` block
  whose `fit_end_for_inference` equals the window the run resolved (CLI flag or
  model meta), and whose `model_pkl_sha256` equals the SHA-256 of the pickle
  that produced the scores

#### Scenario: a bundle without an integrity stamp does not fake an identity
- **WHEN** the provider bundle carries no `_fetch_integrity` identity
- **THEN** `meta.bundle_tag` is `null` — never a fabricated or defaulted tag

#### Scenario: legacy artifacts remain readable, distinguishably
- **WHEN** a reader loads a pre-v2 JSON (no `meta`, no `artifact_schema_version`)
- **THEN** parsing succeeds and the absence is DETECTABLE (readers can branch on
  the missing block); readers surfacing generation context SHALL warn rather
  than substitute defaults

### Requirement: ensemble 晨跑参数 SHALL 从钉死 serving config 绑定且显式不等即拒

`--ensemble-manifest` 模式下，宇宙/再平衡节奏/topk 三参数 SHALL 按下列语义解析：未显式给出时 SHALL 取 `config/serving/csi800_n5_production.yaml`（两级绑定链锚定件）的绑定值；显式给出且与绑定值不等 SHALL 拒绝出单（fail-loud，不静默以任一方覆盖另一方）；绑定源缺失、不可解析或缺任一绑定键 SHALL 拒绝（ensemble 模式必须有绑定源）。legacy 单模型模式 SHALL 保持原缺省与行为逐字不变——缺省语义的翻转会让 csi300 时代模型踩进 csi800 打分禁配。

#### Scenario: ensemble 模式一行命令绑定齐全

- **WHEN** 仅以 `--ensemble-manifest <路径>` 调用晨跑
- **THEN** instruments/rebalance_cadence_days/topk 取绑定值
  （csi800/5/50），产物携 iso-week 再平衡字段

#### Scenario: 显式参数与绑定值不等时拒绝

- **WHEN** ensemble 模式下显式给出与绑定值不等的
  `--instruments`/`--rebalance-cadence-days`/`--topk`
- **THEN** 拒绝出单并指出不等的参数与两侧值——显式参数不是绕过
  绑定链的通道

#### Scenario: 绑定源缺失时拒绝

- **WHEN** ensemble 模式下 serving config 缺失/不可解析/缺绑定键
- **THEN** 拒绝出单（绝不回退到 CLI 缺省——那正是漏参陷阱本身）

#### Scenario: legacy 单模型路径行为不变

- **WHEN** 以 `--model`（或缺省 canonical 路径）调用且未显式给出
  三参数
- **THEN** 沿用原缺省（csi300/1/50），不读 serving config

### Requirement: 自举中止后的三元组重注册 SHALL 走新提案且如实入档

首次自举被任一门拒绝而中止后，若操作人裁决继续晋升，重注册 SHALL 以**新 OpenSpec 提案**进行，且满足全部下列义务：

1. 新三元组窗口 SHALL 按与原注册**同源的冻结公式**在当前 bundle
   尾重新推导（T-6m/T-3m/T 错峰、24 月滚动训窗 + 3 月 valid、
   交易日吸附），推导规则与结果表 SHALL 写入提案供签署；
2. 被拒轮次的**全部门工件（含 FAIL 件）** SHALL 随提案入库
   （evidence 目录），FAIL 工件永不删除；
3. 提案 SHALL 显式披露新旧窗口的任何重叠，并声明门判据与工装
   零改动——公式重锚定 SHALL NOT 构成对失败窗口的挑选；
4. 被拒轮次已训成员（含通过成员级门者）SHALL 全体弃置，新三元
   组三名成员 SHALL 全部重训——不得混装新旧窗口成员；
5. 重注册提案 merge 之前，任何点火 SHALL NOT 发生（merge = 新窗
   冻结生效，预注册纪律与原注册同权）。

#### Scenario: 重注册后旧窗口成员不得晋升

- **WHEN** 重注册提案 merge 后，切换执行器收到按旧（被拒轮次）
  窗口训练的成员
- **THEN** 预注册窗口绑定按新冻结值逐位比对失败，成员被拒——
  旧窗口成员（含曾过门者）无晋升路径

#### Scenario: 被拒轮次证据不可湮灭

- **WHEN** 重注册提案入库
- **THEN** 被拒轮次的三门工件与拒绝简报以 evidence 形式入库，
  后续任何变更 SHALL NOT 删除或改写 FAIL 工件

### Requirement: 生产服务 SHALL 以 iso-week 节奏披露再平衡日语义

生产服务（daily_recommend）SHALL 按 **每 ISO 周第一个交易日 =
再平衡日** 的锚判定当日角色，并在输出工件中携带
`rebalance_day: true|false` 与 `next_rebalance_date` 字段（HOLD
日的工件语义由上文 MODIFIED 的 buy-list requirement 唯一定义，
本 requirement 只负责锚判定与字段披露）。周中 ST/退市/停牌事件 SHALL NOT 触发中途
调仓——卖出在下一再平衡日处理，与认证回测的 N5 语义（持有日仅
市场漂移、约束仅在再平衡生效日校验）保持一致。再平衡日判定
SHALL 由交易日历驱动（节假日顺延至该 ISO 周内第一个实际交易日；
整周无交易日则该周无再平衡日），判定逻辑 SHALL 有确定性测试
覆盖（跨年 ISO 周边界、春节长假周、单日交易周）。

#### Scenario: 节假日周锚顺延

- **WHEN** ISO 周第一个日历工作日为节假日
- **THEN** 该周再平衡日 = 该 ISO 周内第一个实际交易日

### Requirement: 生产模型晋升 SHALL 以 certify 侧车与 guard eval 双门把守

晋升路径的执行 SHALL 满足下列全部前置，缺一即拒绝执行。本
requirement 管辖**晋升路径**：首次生产切换（自举 ensemble
上线）与任何策略级变更（universe/cadence/约束/成本口径改动）；
**季度成员轮换是独立的维护路径**（见"生产打分 SHALL 实现认证
协议本体"requirement——codex #389 r1：轮换不改策略语义，SHALL
NOT 重跑侧车/iso_week 门，其前置在彼处定义）。**零写入的范围
限于晋升执行本体**（canonical pkl/meta 替换、备份件、基线
记录），失败路径的审计记录（guard eval 产物、如实入档文本）
SHALL 照常写入，二者不冲突（失败必须留痕，canonical 必须
不动）：

1. **战役资格门**：已提交 verdict 侧车经
   `csi800_campaign_certify.py --verify` 复验通过且
   `promotion_eligible: true`（晋升资格唯一权威，沿
   `v2-csi800-expansion-guards`）；
2. **iso_week 复核门（锚定工件，codex #385 r3）**：复核 run 的
   证据 SHALL 已提交至钉死证据路径并从 `origin/main` 可达锚经
   `git show` 读取（与战役 certify 同口径）——门 SHALL 从锚上
   字节验证：(a) 复核 run 内嵌 config 绑定已提交的 iso_week
   复核 preset（config 哈希比对）；(b) 全窗净超额年化 > 0 由
   锚上 report 重导，非操作人断言。本地/未锚定的复核输出
   SHALL 被拒绝——生产锚（iso-week）与认证胜者锚（fold_phase）
   是不同 schedule，未经锚定复核的锚漂移 SHALL NOT 进入生产
   绑定；
3. **per-retrain 轻门（R1 修订，替代已废止的"冻结单年净>0"门
   ——该门与协议级认证证据结构性错配，实证见
   `docs/research/csi800_n5_promotion_guard_brief.md`）**：每名
   新成员进入生产 ensemble 前 SHALL 全过：(a) trainer 完整性
   （best_iteration/valid loss 有限，且 best_iteration SHALL NOT
   等于 num_boost_round——早停从未触发即训练预算耗尽的边界
   异常，codex #389 r12）。**num_boost_round 机读源（codex #389
   r18——现行 sidecar 不含该字段）**：trainer sidecar schema
   SHALL 扩展携带 `num_boost_round`（训练实际使用值，随 sidecar
   写盘），门 SHALL 仅从 sidecar 读取两值比较；sidecar 缺
   `num_boost_round` 字段 = 完整性门失败（fail-closed，SHALL NOT
   回退到 preset 默认值或跳过边界检查）；(b) 退化门
   （新 ensemble 对 trailing quarter 可执行 stamp 0 degenerate /
   0 straddle）；(c) 约束干跑（trailing quarter N5 回测
   campaign_v1 RAISE 零触发）；(d) IC 方向门（valid 窗
   IC(1d) > 0）；(e) serving veto 面：干跑 attribution 上
   veto②/⑤ 数字原样（<80% / <75% / <10%），veto③ = 干跑年化
   换手 ≤ 锚上 iso_week 复核 run 换手均值 ×1.5。**净收益
   SHALL NOT 作为 per-retrain 门**——业绩权威 = 已认证战役证据 +
   年度再认证。**本晋升路径（自举/策略级变更）下的失败动作**
   （codex #389 r14——与轮换维护路径的"沿用旧 ensemble/两季
   升级"动作严格分离，后者仅存在于协议本体 requirement）：任一
   门不过 = 切换中止、现任 canonical 及其服务语义不变、失败
   如实入档、升级为操作人决策点，SHALL NOT 出现"沿用旧
   ensemble"分支（自举时旧 ensemble 不存在）；
4. **回滚件义务**：替换前 SHALL 写 pre-promote 备份（pkl + meta，
   带时间戳）并在 `docs/promotion/` 落新基线记录，现任基线保留；
   回滚 SHALL 为恢复备份件的单步操作。

per-retrain 门的全部数字 SHALL 于对应执行 PR 的数字 STOP 首次
呈报，跑后 SHALL NOT 修改判据或数字。

#### Scenario: 侧车缺失或复验失败时拒绝晋升

- **WHEN** 晋升工具在无已提交侧车、或 `--verify` 失败、或
  `promotion_eligible != true` 的状态下被调用
- **THEN** 拒绝执行，canonical 工件（pkl/meta/备份/基线）零
  写入，失败原因记录写入审计档，报错指向缺失的前置

#### Scenario: 晋升路径下任一轻门不过即中止切换

- **WHEN** 自举（或策略级变更）中任一轻门失败
- **THEN** 切换中止、现任 canonical 及其服务语义不变、门工件与
  结果如实入档、升级为操作人决策点（轮换维护路径的"沿用旧
  ensemble/两季升级"动作不适用于本路径）

#### Scenario: 训练预算耗尽的成员触发晋升路径中止

- **WHEN** 晋升路径（自举/策略级变更）中某成员的 trainer
  sidecar 记录 `best_iteration == num_boost_round`（早停从未
  触发）
- **THEN** trainer 完整性门失败（边界异常非收敛信号）→ 切换
  中止、现任 canonical 及其服务语义不变、失败如实入档、升级为
  操作人决策点（"成员不入 ensemble/沿用现行"是季度轮换维护
  路径专属动作，不适用于本路径；部分 ensemble 继续 SHALL NOT
  发生）

#### Scenario: 未锚定的 iso_week 复核输出被拒绝

- **WHEN** 晋升工具被指向一个仅存在于本地工作树（未提交/未
  合并主线）的 iso_week 复核 report
- **THEN** iso_week 复核门拒绝通过，报错指向锚定义务，canonical
  零写入

### Requirement: 生产打分 SHALL 实现认证协议本体（季度重训 + ensemble 3）

生产打分 SHALL 由**最近三名季度成员模型的 ensemble** 产生（与
walk-forward `apply_ensemble` 同语义——认证战役证据的预测生成
方式），SHALL NOT 以单一冻结模型近似协议（结构性错配已实证，
见 `docs/research/csi800_n5_promotion_guard_brief.md`）。每季度
末 SHALL 训练一名新成员（同族配置：Alpha158/LGB/csi800/campaign
三守卫，24 个月滚动训窗 + 3 个月 valid，embargo 同 walk-forward
折算术），经 per-retrain 轻门后轮换进 ensemble（最老成员退出）。
serving SHALL 经 manifest 消费三成员（pkl + meta 逐一列出，
视为一个逻辑模型；manifest 缺员/断链 SHALL fail-loud 拒绝出单）。
**季度轮换是维护路径而非晋升路径（codex #389 r1）**：其前置
SHALL 为且仅为 (a) 现行战役认证有效（已提交 verdict 侧车在库且
年度再认证未过期、未 LOSE）；(b) 新成员通过 per-retrain 轻门；
(c) 轮换前 SHALL 写 pre-rotation manifest 备份（单步回退到上一
ensemble）。**轮换路径的轻门失败动作**（codex #389 r14——与
晋升路径的"中止切换"动作严格分离）：新成员任一轻门不过 =
该成员不入 ensemble、现行 ensemble 沿用、门工件如实入档；
连续两季不过 SHALL 升级为操作人决策点。轮换 SHALL NOT 重跑
侧车 `--verify`/iso_week 门——
它们锚定的是策略语义，成员轮换不改变策略；年度再认证过期或
LOSE 期间轮换路径 SHALL 冻结（升级操作人决策点）。
**有效期锚（codex #389 r2/r5，确定性机读）**：认证有效期 =
**15 个月**（12 个月再认证周期 + 3 个月执行宽限），锚 = **状态
工件路径**在 `origin/main` 的 tip commit committer 日期（
`git log -1 --format=%cI origin/main -- docs/promotion/
csi800_recert_status.json`），SHALL NOT 锚在侧车路径（其非年检
触碰会漂移有效期——codex #389 r5）、SHALL NOT 依赖操作人断言
或本地文件时间戳。
**认证状态单一单调工件（codex #389 r3/r4）**：certify 在 LOSE
时按设计拒写侧车，且跨路径 committer 日期比较对乱序合并不
鲁棒——故认证状态 SHALL 由**单一状态工件**
`docs/promotion/csi800_recert_status.json` 唯一承载：每次年度
再认证（含首次自举）SHALL 将其更新为最新状态并走 PR 入库，
内容 SHALL 含 `verdict: WIN|LOSE`、对应 verdict 侧车的内容
哈希引用（WIN 时）、证据锚 commit 与判定说明。轮换执行器
SHALL 仅经 `git show origin/main:<状态工件路径>` 读取该文件——
**状态由文件内容直接给出，SHALL NOT 以跨路径日期/拓扑推断**；
`verdict: LOSE` 即冻结，新 WIN 状态合并即恢复。15 个月有效期
以状态工件路径在主线的 tip commit committer 日期起算（月级
粗粒度视界，日级合并乱序无实质影响；状态正确性本身不依赖
日期）。状态工件 SHALL 仅由年检流程与首次自举修改（治理
测试钉守——侧车路径的非年检触碰不影响轮换判定）。
**年度再认证义务**：每年 SHALL 以最新数据重跑战役协议全链
（walk-forward + pair/attach/certify）并**更新状态工件**
（codex #389 r6：产物是状态工件的新状态，非无条件的新侧车）——
WIN 时 certify 产新 verdict 侧车且状态工件携其内容哈希引用；
LOSE 时 certify 按设计不写侧车，状态工件单独承载 LOSE 判定。
再认证 LOSE = 生产降级决策点（操作人裁决），季度轻门 SHALL NOT
承担净业绩职责。
**首次自举门语义（codex #389 r13——自举时无旧 ensemble 可回退，
门的对象与失败动作须显式定义）**：首次上线 SHALL 以三名错峰
成员自举（训窗终点 T-6m/T-3m/T）；**成员级门**（trainer 完整性、
valid 窗 IC > 0）SHALL 对三名成员逐一评估；**ensemble 级门**
（退化、约束干跑、serving veto 面②③⑤）SHALL 对组装后的三成员
ensemble 整体跑一次（trailing quarter 干跑）。任一成员级或
ensemble 级门失败 = **自举中止**：不执行切换、现任 canonical
续任、失败如实入档，处置（重训失败成员或另行提案）升级为
操作人决策点——自举无"沿用旧 ensemble"分支，那是季度轮换
维护路径的失败动作。

#### Scenario: 自举任一门失败时不切换

- **WHEN** 三成员自举中任一成员级门（trainer/IC）或 ensemble
  级门（退化/约束干跑/veto 面）失败
- **THEN** 切换不执行、现任 canonical 与其服务语义不变、失败
  门工件如实入档、升级为操作人决策点

#### Scenario: ensemble manifest 缺员时拒绝出单

- **WHEN** serving manifest 声明的三成员中任一 pkl/meta 缺失或
  哈希断链
- **THEN** daily_recommend fail-loud 拒绝出单，报错指向缺失
  成员，绝不静默降级为部分 ensemble 或单模型

#### Scenario: manifest 重复成员身份时拒绝出单（codex #390 r4）

- **WHEN** serving manifest 的三成员槽位间任一身份字段重复
  （`pkl_path`/`pkl_sha256`/`meta_path`/`meta_sha256`——含同一
  pickle 内容以不同路径拼写出现）
- **THEN** manifest 加载 fail-loud 拒绝（重复成员会把三成员
  ensemble 静默退化为均值意义上的单/部分模型），拒绝发生在
  触碰任何模型字节之前

#### Scenario: 成员框架版本漂移时拒绝出单（codex #390 r3）

- **WHEN** serving 加载某成员时，其 trainer sidecar 记录的训练
  框架版本（按 sidecar `model_type` 对应的框架）与 serving 环境
  已安装版本不一致、该框架不可导入、或 sidecar 无法解析/缺
  `model_type`/缺版本字段/其 `pkl_sha256` 与 manifest 矛盾
- **THEN** daily_recommend fail-loud 拒绝出单（框架 minor 升级
  可静默改变 booster 序列化语义——walk-forward ensemble 的
  sidecar 版本守卫同语义，但 serving 拒绝而非跳过）

#### Scenario: ensemble 工件身份字段语义（codex #390 r3）

- **WHEN** ensemble 模式产出推荐工件
- **THEN** 工件 meta **不携带** `model_pkl_sha256`（该字段语义
  保留给单模型 pickle 摘要，决策页以其交叉核对 trainer sidecar
  的 `pkl_sha256`——挪用会使合法 ensemble 工件被误报为"其他
  模型"）；ensemble 身份由 `meta.ensemble.manifest_sha256` 承载，
  `model_path` 指向 manifest，成员三元组逐一列出

#### Scenario: 年度再认证 LOSE 触发降级决策点

- **WHEN** 年度再认证 walk-forward 全链产出 LOSE 判定
- **THEN** 结果如实入档并升级为操作人决策点（回滚/停用），
  生产 ensemble 在裁决前不自动变更

#### Scenario: 轮换 SHALL 绑定 gate 工件的被测窗口（codex #391 r19）

- **WHEN** 轮换执行器消费一份 `overall: PASS` 且摘要绑定正确的
  gate 工件，但其 `window` 块缺失、非日期、跨度越界、结束日远早于
  轮换时刻（陈旧）或落在未来；或成员级工件的 valid 窗未严格晚于
  该成员训窗终点（非样本外）/ 起点距训窗终点过远（并非该成员的
  valid 窗）
- **THEN** 轮换拒绝执行（manifest 零写入）——摘要绑定只证明"门测
  了哪些工件"，被测窗口绑定才能排除"在更容易的时期或陈旧时期测
  出的 PASS"；ensemble 级 trailing quarter 允许与训窗重叠（其职责
  是行为面而非业绩，R1 无净收益门），仅受时效与跨度约束

#### Scenario: 自举成员窗口豁免时效界而 ensemble 干跑窗不豁免（PR-C'）

- **WHEN** 首次自举的切换执行器消费三名成员的 gate 工件——其 valid
  窗按 R1-DP-C 的错峰设计（训窗终点 T-6m/T-3m/T）**刻意落在过去**
- **THEN** 成员级工件豁免"被测窗口须临近当前时刻"的时效界（其余
  绑定——样本外、紧邻训窗、跨度、不落未来、摘要与 fit 窗绑定——
  一律照旧）；**ensemble 级 trailing quarter 干跑窗不豁免**（它必须
  描述当下）。豁免仅存在于自举路径，季度轮换维护路径无此豁免

#### Scenario: 再认证过期期间季度轮换被冻结

- **WHEN** 年度再认证已过期（或 LOSE 未裁决）时尝试季度成员轮换
- **THEN** 轮换路径拒绝执行（manifest 零写入），升级为操作人
  决策点——维护路径的合法性以现行认证有效为前提

### Requirement: 生产服务参数 SHALL 经两级治理绑定链锚定认证胜者

生产服务参数 SHALL 经**两级恰差链**锚定认证胜者，且 SHALL NOT
经白名单吸收锚漂移——生产锚（iso-week）与认证胜者锚
（`fold_phase`）在 `v2-rebalance-cadence` 下是不同 schedule：

1. **iso_week 复核 preset**（`csi800_cadence5_conservative_isoweek`
   ，7b 预承诺的胜者复核切片落地形态）与认证胜者 preset
   `csi800_cadence5_conservative.yaml` 恰差
   **{rebalance_anchor, output_dir}**，治理测试钉死；其复核 run
   净超额年化 > 0 是晋升门的一部分（见晋升门 requirement）；
2. **生产服务侧参数**与 iso_week 复核 preset 的语义字段恰差
   SHALL 仅限服务侧必要字段（白名单跑前写死入治理测试），
   universe / benchmark / cadence 数值语义 / 约束校准 / 作用域 /
   成本口径 SHALL 同值。

20 bps 保守成本口径与 73 bps 盈亏平衡参考 SHALL 记入运维
runbook 作为预期管理基准；观察期纪律（首季度只记录不回调）
SHALL 同步入档。

#### Scenario: 服务参数漂移被治理测试拦截

- **WHEN** 有人修改生产服务参数使其与 iso_week 复核 preset 的
  语义字段产生白名单之外的差异
- **THEN** 治理测试失败，指出漂移字段与所需的 OpenSpec 变更路径

#### Scenario: 锚漂移不得经白名单逃逸

- **WHEN** 有人试图把 rebalance_anchor 加入服务侧白名单以绕过
  iso_week 复核
- **THEN** 治理测试失败——锚差异仅存在于第一级恰差
  {rebalance_anchor, output_dir}，且该级以复核 run 过线为前提

