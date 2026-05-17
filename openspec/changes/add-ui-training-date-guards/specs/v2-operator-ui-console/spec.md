## ADDED Requirements

### Requirement: Operator UI SHALL guard training date ranges before launch

The operator UI SHALL validate training date ranges before launching a
UI-managed pipeline job. The UI SHALL reject malformed dates and non-strict
train/valid/test ordering before `JobManager.start()` is called.

#### Scenario: validation overlaps training

- **WHEN** `train_end` is on or after `valid_start`
- **THEN** the UI displays a blocking validation error
- **AND** the Run button is disabled
- **AND** no job is started

#### Scenario: test overlaps validation

- **WHEN** `valid_end` is on or after `test_start`
- **THEN** the UI displays a blocking validation error
- **AND** the Run button is disabled
- **AND** no job is started

---

### Requirement: Operator UI SHALL preview provider coverage when metadata exists

The operator UI SHALL preview provider coverage when a selected `provider_uri`
has adjacent provider metadata artifacts. The preview SHALL display coverage,
calendar, instrument count, and validation health. Metadata preview SHALL be
read-only and SHALL NOT initialize qlib or compute runtime metrics.

#### Scenario: Tushare provider metadata exists

- **WHEN** `provider_uri` points at a generated `qlib_provider` directory
- **AND** provider-adjacent `validation.json` or `manifest.json` exists
- **THEN** the UI displays the provider coverage dates and health
- **AND** this preview does not call qlib data APIs

---

### Requirement: Operator UI SHALL guard provider tail dates for backtests

When the selected provider exposes a trading calendar, the operator UI SHALL
reject pipeline runs whose `test_end` is on or after the provider's final
calendar date. The UI SHOULD warn when fewer than twenty provider trading days
remain after `test_end`, because forward-return signal summaries may be
incomplete near the provider tail.

#### Scenario: test end is provider final date

- **WHEN** provider calendar ends on `2025-12-31`
- **AND** `test_end` is `2025-12-31`
- **THEN** the UI displays a blocking validation error
- **AND** suggests using an earlier test end or pulling more data

#### Scenario: test end has short forward buffer

- **WHEN** fewer than twenty provider trading days remain after `test_end`
- **THEN** the UI displays a non-blocking warning
- **AND** the warning explains that forward-return summaries near the tail may
  be incomplete

---

### Requirement: Operator UI SHALL guard named instrument universes

The operator UI SHALL guard named instrument universes when the selected
provider exposes instrument universe files. The UI SHALL reject named
instrument universes that do not have a corresponding `instruments/<name>.txt`
file. Explicit `all` remains valid when `all.txt` exists.

#### Scenario: missing csi300 universe

- **WHEN** the selected provider has `instruments/all.txt`
- **AND** the operator enters `csi300`
- **AND** `instruments/csi300.txt` does not exist
- **THEN** the UI displays a blocking validation error
- **AND** no job is started
