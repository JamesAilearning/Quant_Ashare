## ADDED Requirements

### Requirement: Today Workbench SHALL remain a read-only operating summary

The operator UI SHALL provide a Today Workbench page that aggregates existing
bundle-health, data-update-status, incumbent-identity, daily-recommendation,
and job artifacts. The page SHALL NOT spawn a subprocess, write an artifact,
calculate an official metric, or invoke a training, backtest, factor-mining,
or order-execution path.

#### Scenario: operator opens the workbench
- **WHEN** an operator opens the Today Workbench
- **THEN** the page presents only facts obtained from existing read-side
  helpers and artifacts
- **AND** it states that the page neither runs a strategy nor creates an order

### Requirement: Workbench SHALL distinguish informational health from production authorisation

The workbench SHALL display data health, last data-update state, and serving
identity as distinct informational summaries. Bundle health or a parseable
serving manifest SHALL NOT be presented as a promotion, certification, or
trading-authorisation verdict. The page SHALL link the operator to the
production-governance page for those detailed checks.

#### Scenario: serving identity is resolvable
- **WHEN** the existing incumbent resolver identifies an ensemble or an
  explicit single-model serving shape
- **THEN** the workbench displays that identity as an informational fact
- **AND** it does not label the model certified or authorised solely from that
  identity

### Requirement: Workbench SHALL fail closed on recommendation provenance

The workbench SHALL classify the newest dated recommendation artifact with the
same filename/date, artifact-shape, incumbent-provenance, and cadence helpers
used by the detailed daily-decision page. It SHALL display HOLD or rebalance
state only when the artifact is structurally valid and provenance matches the
current incumbent. A legacy, malformed, mismatched, or unverifiable artifact
SHALL display an explicit verification-required state and SHALL NOT be
presented as a current production signal.

#### Scenario: newest artifact matches the current incumbent
- **WHEN** the newest artifact has a matching filename/payload date, valid
  metadata, and a provenance verdict that matches the current incumbent
- **THEN** the workbench displays its HOLD or rebalance state with its recorded
  signal and entry dates

#### Scenario: newest artifact cannot be bound to the incumbent
- **WHEN** the newest artifact is legacy, malformed, mismatched, or cannot be
  verified against the current incumbent
- **THEN** the workbench displays "needs verification"
- **AND** it does not display HOLD or rebalance as an actionable state

#### Scenario: newest artifact has an invalid candidate list
- **WHEN** the newest artifact otherwise matches the current incumbent but its
  `picks` value is missing, is not a list, or contains a non-object member
- **THEN** the workbench SHALL use the detailed page's candidate-list
  validation boundary
- **AND** it SHALL display "needs verification" rather than HOLD, rebalance,
  or a current daily signal

### Requirement: Workbench SHALL surface operational exceptions without owning jobs

The workbench SHALL display a read-only operational summary derived from the
existing unified job list. An in-flight job (pending or running) takes
precedence; otherwise the most recent failed, partial, or stopped job is
surfaced. The page SHALL link to the Jobs page for detail and SHALL NOT start,
stop, delete, or mutate a job.

#### Scenario: a job is in flight
- **WHEN** the unified job list contains one or more pending or running jobs
- **THEN** the workbench identifies that work is in progress and links to Jobs

#### Scenario: no job is running and a recent job failed
- **WHEN** no job is running and the most recent terminal job failed
- **THEN** the workbench surfaces the failed state and links to Jobs

### Requirement: Navigation SHALL follow the operator task flow

The application navigation SHALL retain all current operator pages and group
them under `日常决策`, `研究与验证`, and `生产治理`. The Today Workbench,
Run Center, and daily-signal review SHALL appear under `日常决策`; research
runs and their results under `研究与验证`; and operations plus data inspection
under `生产治理`.

#### Scenario: operator reads the navigation
- **WHEN** the operator UI renders its navigation
- **THEN** every existing page remains reachable in exactly one task-oriented
  group
- **AND** the Today Workbench is the first daily-decision destination
