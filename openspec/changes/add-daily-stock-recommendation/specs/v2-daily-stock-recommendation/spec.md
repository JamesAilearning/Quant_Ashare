## ADDED Requirements

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
