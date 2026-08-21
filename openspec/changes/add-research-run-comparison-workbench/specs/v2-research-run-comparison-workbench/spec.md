## ADDED Requirements

### Requirement: Select bounded research runs from the existing catalog

The operator UI SHALL provide a research-only comparison workbench that lets
an operator select two through five existing pipeline or walk-forward runs
from the existing unified job catalog.  It SHALL keep the selected full run IDs
in the URL and restore a valid selection from that URL.  The workbench SHALL
not launch, cancel, mutate, delete, or otherwise operate on a run.

#### Scenario: A valid URL restores two selected runs

- **WHEN** an operator opens the comparison page with two known selected run
  IDs in its URL parameter
- **THEN** the page selects those exact run IDs
- **AND** renders only read-only comparison controls and artifacts

#### Scenario: The selection is outside the allowed size

- **WHEN** fewer than two or more than five runs are selected
- **THEN** the page explains the two-to-five requirement
- **AND** does not assign a comparison rank

#### Scenario: A CLI row only overlaps a UI job in time

- **WHEN** a CLI catalog row shares a run directory and overlapping timestamps
  with a UI lifecycle row but does not producer-record that UI job ID
- **THEN** the workbench keeps the CLI row as the current artifact owner
- **AND** does not alias its run ID to the UI row

### Requirement: Establish comparability before controlled ordering

Before assigning a controlled research ordering, the workbench SHALL compare
the selected runs' universe, training/validation/testing windows, benchmark,
signal-to-execution lag, canonical cost/exchange controls, and data
provenance including the producer-recorded calendar-content identity and
rebuild identity.  A rebuild identity SHALL originate from a producer-written
bundle build/publish stamp, so a corrected feature or instrument bin under an
unchanged calendar cannot compare equal to the previous bundle or reuse its
feature-dataset cache entry.
A missing, malformed, or unequal required value SHALL block the ordering and
identify the affected field and run ID.  The workbench SHALL NOT invent a
missing value, recompute metrics, or call a non-comparable selection equivalent.
For a walk-forward run, the aggregate report SHALL expose canonical-backtest
comparison provenance only when every persisted fold report records matching
canonical official-backtest path, execution semantics, ST-mask identity, and
runtime/bundle identity.  Missing fold evidence or disagreement between folds
SHALL remain explicit and SHALL block controlled ordering.

#### Scenario: Equal complete contracts permit an ordering

- **WHEN** two or more selected runs have complete and equal required
  comparison-contract values
- **THEN** the page may show a stable read-only ordering by the existing
  reported information ratio
- **AND** labels the ordering as research-only and `扣费后超额` in meaning

#### Scenario: A data-provenance value is unavailable

- **WHEN** one selected run has no canonical runtime provenance in its report
- **THEN** the page labels that value unavailable with its reason
- **AND** blocks metric ordering for the whole selection

#### Scenario: A provider path has no immutable bundle identity

- **WHEN** a selected run records only a mutable provider URI or an unavailable
  bundle-content identity
- **THEN** the page labels data provenance unavailable
- **AND** blocks metric ordering rather than treating the path as data identity

#### Scenario: A rebuild changes bytes under an unchanged calendar

- **WHEN** two selected runs have the same calendar-content identity but
  different producer-recorded bundle rebuild identities
- **THEN** the page blocks controlled ordering as data-provenance mismatch
- **AND** never scans all bundle files while reading the comparison page

#### Scenario: Walk-forward folds disagree on canonical provenance

- **WHEN** persisted fold reports in one walk-forward run contain different
  canonical-backtest comparison provenance
- **THEN** its aggregate report marks that provenance as mixed
- **AND** the workbench blocks controlled ordering rather than selecting an
  arbitrary fold's value

#### Scenario: A walk-forward aggregate lacks a canonical path across folds

- **WHEN** an aggregate declares official metrics but its persisted fold
  evidence has a missing, non-canonical, or mismatched official-backtest path
- **THEN** the aggregate does not qualify as canonical comparison evidence
- **AND** the workbench blocks controlled ordering for that run

#### Scenario: A bundle changes during a walk-forward run

- **WHEN** either bundle identity changes between walk-forward fold boundaries
- **THEN** the engine refuses to continue or publish an aggregate report
- **AND** it directs the operator to restart from one bundle generation

#### Scenario: A bundle changes during a pipeline run

- **WHEN** a provider calendar-content or rebuild identity changes after
  pipeline feature construction begins and before its report is published
- **THEN** the pipeline refuses to publish the report
- **AND** its backtest provenance remains bound to the identities captured
  before feature construction rather than restamping from the changed provider

### Requirement: Present existing evidence and precise read-only references

For every selected run, the workbench SHALL display only values already present
in its artifacts, including available metrics, model/configuration identity,
data provenance, and walk-forward fold stability evidence where applicable.
It SHALL distinguish unavailable values from zero or success.  It SHALL offer
the exact run ID's Results link, Walk Forward link when applicable, and
read-only configuration and log references.

#### Scenario: A selected walk-forward run has fold evidence

- **WHEN** a selected run has a valid `walk_forward_report.json` with fold
  records whose declared positive `num_folds` equals the fold-record count
  and whose valid-fold count equals the listed finite `information_ratio` values,
  and whose per-fold metric statuses and prediction shapes support the recorded
  aggregate metric status
- **THEN** the page displays its existing aggregate metrics and fold stability
  evidence without calculating replacement fold metrics

#### Scenario: Fold counts contradict the listed records

- **WHEN** a walk-forward report declares a fold count different from its fold
  list length, a zero fold count, a valid-fold count different from its listed
  finite IR values, a fold row omits its index, information-ratio, status, or
  prediction shape, or a measured fold's status contradicts the aggregate status
- **THEN** the page marks fold stability evidence invalid
- **AND** does not display the contradictory counts as evidence

#### Scenario: A required artifact is missing or invalid

- **WHEN** a selected run's configuration or report artifact is missing,
  unreadable, malformed, or outside the output read boundary
- **THEN** the page presents an explicit needs-verification issue
- **AND** blocks controlled ordering rather than treating the absent artifact
  as an empty or comparable result

#### Scenario: A pipeline configuration is only a partial submit-time input

- **WHEN** a selected pipeline run's `config.yaml` omits any resolved
  `PipelineConfig` field
- **THEN** the page marks the configuration as invalid evidence
- **AND** does not reconstruct omitted values from current defaults

#### Scenario: Pipeline configuration and report are from different runs

- **WHEN** a complete pipeline `config.yaml` disagrees with its
  `pipeline_report.json` on a producer-written shared field such as universe,
  window, or canonical execution control
- **THEN** the page marks the artifact pair as inconsistent evidence
- **AND** blocks controlled ordering instead of showing the configuration as
  the source of the reported metrics

#### Scenario: Official pipeline path projections disagree

- **WHEN** a pipeline report labels metrics official but its top-level,
  `backtest.provenance`, and `comparison_provenance` canonical paths are
  missing, non-canonical, or unequal
- **THEN** the page marks the metric path unverified
- **AND** blocks controlled ordering

#### Scenario: Pipeline artifacts use equivalent runtime spellings

- **WHEN** a complete pipeline `config.yaml` records a relative, user-home, or
  case-variant provider path, or an uppercase supported region, and the
  producer-written report records the canonically normalized runtime values
- **THEN** the page treats those runtime controls as equal
- **AND** resolves a config-side `null` stamp-tax schedule to the canonical
  default before comparing it with the report's producer-written expanded
  schedule
- **AND** blocks ordering when that expanded report schedule differs
