# v2-daily-stock-recommendation Specification (delta)

## ADDED Requirements

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
