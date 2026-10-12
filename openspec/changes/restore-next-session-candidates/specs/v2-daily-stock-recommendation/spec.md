## MODIFIED Requirements

### Requirement: Daily recommendation SHALL resolve the as-of date to a real trading day

Default T SHALL be the latest fully closed session and SHALL equal the price provider's tail. The planned entry date SHALL be the first open session strictly after T in a separate, explicit, provenance-bound Tushare SSE schedule; it need not have price bars. An explicit as-of SHALL be an actual recorded price session no later than the latest closed session. Missing/stale schedule, non-session or future T, conflicting price/calendar dates, missing next open day, or a lagging default provider SHALL fail loudly before loading models or building features. Recommendation SHALL NOT fetch data online, append future price sessions, or revert to penultimate-day historical confirmation.

#### Scenario: Default uses the latest closed price session
- **WHEN** the provider ends at the latest fully closed session and the schedule includes its next open day
- **THEN** `as_of_date` equals the provider tail and `entry_date` equals that planned next open day
- **AND** no entry-day market data is read

#### Scenario: Invalid or stale date evidence is rejected
- **WHEN** the provider lags the latest closed session or the requested T is not an eligible recorded session
- **THEN** an explicit error is raised and no candidate artifact is produced

### Requirement: Daily recommendation SHALL exclude untradable names from the buy list

The path SHALL reuse the canonical microstructure mask on T bars only, excluding known T suspension and one-price-lock conditions from candidate eligibility. ST and approved quarantine exclusions SHALL retain their current evidence and precedence. `eligible_as_of` SHALL mean eligibility under T bars plus the current ST/quarantine view; it SHALL NOT claim historical PIT-ST fidelity or future tradability. Entry-day bars SHALL NOT be requested. All output rows SHALL carry `tradable_flag=null` and explicit unconfirmed entry status, even for excluded rows. Audit reasons and `n_masked` SHALL describe observed T-day candidate exclusions, not future execution outcomes.

#### Scenario: A known T-day exclusion is audited without claiming future status
- **WHEN** a stock is suspended or one-price-locked on T
- **THEN** its `eligible_as_of` is false and it is not selected
- **AND** its audit reason records that known exclusion while `tradable_flag` remains null

#### Scenario: Entry-day prices cannot influence candidates
- **WHEN** a future-bar access trap is installed around all market-data queries
- **THEN** a normal recommendation succeeds without any query later than T

#### Scenario: Handler initialization cannot load forward labels
- **WHEN** the real Alpha158 handler initializes its feature and unused label groups
- **THEN** the inference-only label group uses current close, without a forward-return expression
- **AND** the model still receives the unchanged 158-feature inference frame, not labels

### Requirement: Daily recommendation SHALL emit a ranked, dated, persisted buy list

The single recommendation path SHALL rank eligible finite-score candidates stably and emit at most configured Top-K candidates, with contiguous ranks and dated CSV/JSON/full-audit artifacts. Rows SHALL carry `as_of_date`, `entry_date`, `rank` where applicable, security/name/score, strict boolean `eligible_as_of`, nullable `tradable_flag` fixed to null, and the candidate exclusion reason. JSON and both CSVs SHALL disclose `recommendation_mode=next_session_candidate` and `entry_tradability_status=unconfirmed`; empty CSVs SHALL retain the full header. Counts SHALL use eligibility, not nullable tradability. N5/HOLD SHALL retain the existing T-anchored ISO-week schedule: HOLD remains monitoring-only, and a rebalance day is only a candidate review opportunity, not an executable order. No recommendation SHALL automatically alter holdings or place trades.

#### Scenario: Rebalance candidate is not an execution instruction
- **WHEN** T is an approved N5 rebalance day
- **THEN** the candidate artifacts retain the N5 fields and explicitly require human execution-time checks
- **AND** they SHALL NOT claim entry-day tradability or completed execution

#### Scenario: HOLD candidate preserves cadence
- **WHEN** T is not a rebalance day
- **THEN** the artifact remains HOLD/monitoring-only, with the unchanged next-rebalance-date semantics

### Requirement: The recommendation artifact SHALL carry its own generation context (schema v2)

New outputs SHALL use `artifact_schema_version: 3` and retain the required generation, resolved fit-window, provider/bundle, universe/Top-K, and mutually exclusive single-model versus ensemble identities. The required `meta` SHALL additionally bind the schedule path, same-buffer SHA-256, source API/exchange/query/acquisition timestamp, and generation's latest closed session. Single-model `model_pkl_sha256` SHALL remain exclusive of ensemble identity. `run_meta` SHALL remain required. Candidate mode/status, null tradability, eligibility, finite scores, dates, counts, cadence, quarantine and schedule context SHALL be validated before any output file is created. Missing fields SHALL NOT receive inferred defaults.

#### Scenario: New output is self-describing and consistently projected
- **WHEN** a successful recommendation is written
- **THEN** JSON has schema 3 and the real model/bundle/schedule context
- **AND** both CSVs mirror candidate mode/status and per-row eligibility with empty nullable-tradability cells

#### Scenario: Malformed v3 cannot partially publish or downgrade
- **WHEN** required context is missing, a boolean replaces unknown tradability, or counts disagree with the audited rows
- **THEN** the writer refuses before creating files and readers refuse rather than interpreting the object as v2

#### Scenario: Existing model and provider identities remain mandatory
- **WHEN** a v3 object loses required generation fields or carries both single-model and ensemble identities
- **THEN** the shared writer/reader contract rejects it without defaults or model reloading

#### Scenario: Ensemble metadata cannot shrink or duplicate its identity
- **WHEN** a v3 ensemble declares other than three members or repeats a member's pickle path or content SHA
- **THEN** both writer and reader reject that producer-impossible identity even when its manifest SHA is unchanged
- **AND** no model, sidecar, fit-window or weight certification is reimplemented in the artifact validator

#### Scenario: Invalid cadence cannot leave partial output
- **WHEN** cadence fields are incomplete, a rebalance day does not point to T, or a non-null HOLD next date is malformed, before entry, or on a weekend
- **THEN** the writer refuses before creating or changing any output file
- **AND** a null HOLD next date remains valid without inventing future recorded price sessions

#### Scenario: Legacy files remain historical only
- **WHEN** a reader loads a supported v2 or older distinguishable artifact
- **THEN** it can display its historical meaning without converting it to current candidates, executions or holdings
