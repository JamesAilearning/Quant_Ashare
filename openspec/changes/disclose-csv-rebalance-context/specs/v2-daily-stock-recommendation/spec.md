## MODIFIED Requirements

### Requirement: The recommendation artifact SHALL carry its own generation context (schema v2)

`write_outputs` SHALL serialize a top-level `meta` block into
`daily_recommendation_{as_of}.json` together with `artifact_schema_version: 2`.
The block SHALL carry: `generated_at` (Asia/Shanghai ISO8601 with explicit
offset), `model_path`, the mode-specific identity fields below,
`fit_start_for_inference` / `fit_end_for_inference` (the RESOLVED window the run
actually used), `provider_uri`, `bundle_tag` (the `_fetch_integrity` identity
compact tag, or `null` when the bundle carries no identity stamp), `instruments`
and `topk`.

**Identity shape is mode-exclusive (XOR)**: in single-model mode the meta
SHALL carry `model_pkl_sha256` (SHA-256 of the loaded model pickle) and no
`ensemble` block; in ensemble mode the meta SHALL carry
`meta.ensemble.manifest_sha256` (plus the member triple listing) and SHALL
NOT carry `model_pkl_sha256` — that field's semantics stay reserved for the
single-model pickle digest, which the decision page cross-checks against the
trainer sidecar (see "ensemble 工件身份字段语义"). An artifact carrying both
or neither identity is malformed. The meta SHALL be assembled inside `recommend()` (the single place
that knows the resolved window, model and bundle) and carried on
`DailyRecommendationResult.run_meta` as a REQUIRED field — no default value, so
every constructor (including tests) is forced to supply it rather than silently
omitting context. Generation metadata SHALL remain JSON-only. Daily-mode
buy-list and scored-audit CSVs SHALL remain unchanged; cadence-enabled CSVs
SHALL additionally project the existing cadence fields under the CSV cadence
requirement below, without changing JSON pick rows or schema version.

#### Scenario: a fresh run writes a self-describing artifact
- **WHEN** `recommend()` completes and `write_outputs` persists the JSON
- **THEN** the JSON contains `artifact_schema_version: 2` and a `meta` block
  whose `fit_end_for_inference` equals the window the run resolved (CLI flag or
  model meta), and whose mode-exclusive identity holds: single-model mode
  carries `model_pkl_sha256` equal to the SHA-256 of the pickle that produced
  the scores; ensemble mode carries `meta.ensemble.manifest_sha256` and no
  `model_pkl_sha256`

#### Scenario: a bundle without an integrity stamp does not fake an identity
- **WHEN** the provider bundle carries no `_fetch_integrity` identity
- **THEN** `meta.bundle_tag` is `null` — never a fabricated or defaulted tag

#### Scenario: legacy artifacts remain readable, distinguishably
- **WHEN** a reader loads a pre-v2 JSON (no `meta`, no `artifact_schema_version`)
- **THEN** parsing succeeds and the absence is DETECTABLE (readers can branch on
  the missing block); readers surfacing generation context SHALL warn rather
  than substitute defaults

## ADDED Requirements

### Requirement: Recommendation CSVs SHALL preserve cadence context without changing stock rows

`write_outputs` SHALL append `rebalance_day` and `next_rebalance_date`, in that order, to both `daily_recommendation_<T>.csv` and `daily_recommendation_<T>_scored_full.csv` when the result's `rebalance_day` is not None. Each row SHALL mirror the same result values emitted at the JSON top level. The CSV projection SHALL NOT recalculate cadence, alter dates, ranks, scores, row counts, JSON picks, or the input scored dataframe. None for the next anchor SHALL serialize as an empty CSV cell and remain JSON null; it SHALL NOT be replaced with `entry_date` or another inferred date.

CSV False SHALL mean HOLD/monitoring-only, not an entry instruction, regardless of a row's tradable flag. The existing terminal/operator presentation SHALL retain its explicit HOLD notice; CSV columns are a machine-readable projection, not a new status authority. Empty CSVs SHALL contain the corresponding header and zero data rows; consumers SHALL consult the sibling JSON for actual run-level cadence values. An old CSV lacking cadence fields SHALL NOT be treated as proof of a daily/rebalance run without its JSON context.

When `rebalance_day` is None, CSV bytes/columns and JSON cadence-field absence SHALL remain unchanged. Before any filesystem I/O, the writer SHALL reject a non-None, non-bool marker with `DailyRecommendationError`, rather than accepting numeric or string aliases through equality/truthiness. Existing cadence cross-field validation SHALL remain in force.

#### Scenario: nonempty HOLD and rebalance exports disclose identical context
- **WHEN** a valid cadence-enabled result has one or more stock rows
- **THEN** both CSVs contain the same boolean and next-anchor value as JSON
- **AND** original stock fields, ordering, counts, input dataframe and JSON picks remain unchanged

#### Scenario: a HOLD run has no known next anchor
- **WHEN** the result has `rebalance_day=False` and `next_rebalance_date=None`
- **THEN** each exported stock row has False and an empty next-date cell, while JSON retains null

#### Scenario: an empty cadence export does not invent a stock
- **WHEN** a cadence-enabled buy list or audit frame is empty
- **THEN** the CSV still includes both cadence headers with zero data rows
- **AND** JSON carries the run-level values that the empty CSV cannot encode

#### Scenario: daily-mode exports remain byte compatible
- **WHEN** the result has `rebalance_day=None`
- **THEN** both CSVs retain their prior bytes and headers and JSON omits cadence fields

#### Scenario: a malformed marker cannot cause partial output
- **WHEN** the result's marker is a number, string, or other non-bool non-None value
- **THEN** the writer raises `DailyRecommendationError` before creating or changing any output files
