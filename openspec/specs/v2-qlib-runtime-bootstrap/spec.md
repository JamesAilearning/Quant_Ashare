# v2-qlib-runtime-bootstrap Specification

## Purpose
Define the single canonical qlib runtime initialization boundary and the
anchored official qlib backtest callable used by the V2 runtime.

## Requirements

### Requirement: V2 SHALL provide a single canonical qlib runtime initialization entry point

The system SHALL expose exactly one canonical entry point that initializes qlib
for the canonical runtime layer. All canonical-runtime components SHALL
initialize qlib only through this entry point. The canonical initialization
config SHALL explicitly declare the data provider's price-adjustment convention
using the same supported adjustment-mode vocabulary as the canonical backtest
input.

#### Scenario: canonical runtime initialization is audited
- **WHEN** maintainers inspect the canonical runtime layer
- **THEN** exactly one canonical qlib initialization entry point exists
- **AND** no other canonical-layer module calls `qlib.init` directly
- **AND** the canonical runtime config records the provider adjustment mode

#### Scenario: unsupported provider adjustment mode is rejected
- **WHEN** a caller constructs the canonical qlib runtime config with an
  unsupported provider adjustment mode
- **THEN** a typed `QlibRuntimeInitError` is raised
- **AND** qlib is not initialized with ambiguous adjustment semantics

### Requirement: Canonical qlib initialization SHALL forbid inconsistent re-initialization

The canonical initialization entry point SHALL record the first config it
accepts and SHALL raise a typed error if a subsequent call provides a different
config. Provider adjustment mode SHALL be part of that config comparison.

#### Scenario: conflicting re-initialization is attempted
- **WHEN** a second caller invokes the canonical init with a different
  `provider_uri`, `region`, or provider adjustment mode
- **THEN** a typed `QlibRuntimeInitError` is raised
- **AND** the first init state remains unchanged

#### Scenario: idempotent re-initialization is requested
- **WHEN** a caller invokes the canonical init with the exact same config,
  including provider adjustment mode
- **THEN** the call succeeds without re-initializing qlib

### Requirement: Canonical backtest path SHALL be anchored to a real qlib callable

The canonical backtest contract SHALL expose the canonical official path as a direct reference to an imported qlib callable, so that the path is statically checkable at import time.

#### Scenario: canonical path anchor is reviewed
- **WHEN** maintainers inspect `CanonicalBacktestContract`
- **THEN** `CANONICAL_OFFICIAL_BACKTEST_CALLABLE` resolves to `qlib.backtest.backtest`
- **AND** `CANONICAL_OFFICIAL_BACKTEST_PATH` equals `"qlib.backtest.backtest"`

### Requirement: Canonical runtime layer SHALL NOT reference competing backtest paths

Modules under `src/core/` SHALL NOT reference any alternative qlib backtest entry point (for example `qlib.contrib.evaluate.backtest_daily`).

#### Scenario: alternative backtest path leakage is scanned
- **WHEN** governance regression tests scan `src/core/` source files
- **THEN** no file references `qlib.contrib.evaluate.backtest_daily`
- **AND** only `qlib.backtest.backtest` is used as the canonical entry point
