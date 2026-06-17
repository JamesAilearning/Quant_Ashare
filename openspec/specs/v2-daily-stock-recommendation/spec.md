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

#### Scenario: output is ranked and bounded
- **WHEN** `recommend` produces a result with `topk = 50`
- **THEN** the buy list has at most 50 rows
- **AND** rows are ordered by `predicted_score` descending with
  contiguous ranks `1..N`
- **AND** a `daily_recommendation_<date>.csv` and `.json` are written
  carrying both `as_of_date` and `entry_date`

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

Because excluding ST requires the current name snapshot, the path SHALL
treat that snapshot as REQUIRED: if `name_source_parquet` is unset or the
file is absent, if the snapshot's file date lags the as-of date by more than
`st_snapshot_max_age_days`, or if the snapshot is unreadable / missing the
required `ts_code` or `name` columns / empty, the path SHALL raise an explicit
error and emit no list, rather than silently producing a list that could
include ST names. A snapshot newer than the as-of date SHALL NOT be treated
as stale.

#### Scenario: a missing current-ST source is rejected
- **WHEN** `recommend` is invoked with no `name_source_parquet` (or a path
  that does not exist)
- **THEN** an explicit error is raised and no list is produced

#### Scenario: a stale current-ST snapshot is rejected
- **WHEN** the active-stocks snapshot file's date lags the as-of date by
  more than `st_snapshot_max_age_days`
- **THEN** an explicit error is raised and no list is produced

#### Scenario: a malformed current-ST snapshot is rejected
- **WHEN** the snapshot is present and fresh but is unreadable, is missing
  the `ts_code` or `name` column, or has zero rows
- **THEN** an explicit error is raised and no list is produced
- **AND** the path does NOT fall back to an empty name map that would
  silently disable ST filtering

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

