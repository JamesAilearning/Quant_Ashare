# Data-pipeline contract rules

The data pipeline owns canonical update behavior and its persisted operational
artifacts. UI, scripts, and research readers consume those contracts; they do
not redefine them.

## Contract changes

- Start from the producer and search every consumer before changing a config
  field, report/index/status/ledger schema, exit code, exception, or path
  convention.
- State the schema version, source of truth, lifecycle, and failure behavior.
  Invalid or missing data must fail loudly or be represented explicitly; do not
  add hidden compatibility defaults.
- Keep atomic update, single-flight, cancellation, status, and ledger semantics
  explicit. Observability failures must not silently alter canonical update
  semantics.

## Paired engines and readers

- Pipeline and WalkForward artifacts use parallel schemas. Migrate paired
  writer/reader/test changes together whenever a shared field changes.
- When changing status or ledger artifacts, update every reader and the
  documented exit-code meaning in the same change. A UI reader may not infer a
  terminal state from a stale or malformed record.

## Verification

- Add synthetic regression tests for normal and failure/corruption paths; an
  E2E run with local data is supplementary, not sufficient coverage.
- Import each changed module, run the root-required logic/governance checks and
  OpenSpec validation, and inspect the staged diff before committing.
