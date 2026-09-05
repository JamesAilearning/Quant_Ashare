## Why

The optional real-artifact baseline test assumes every unknowable verdict reads
one extra artifact. A history gap is detected before that read, so a correct
fail-closed runtime verdict currently fails the required full test suite.

## What Changes

- Assert `scanned` against actual reader calls, not inferred terminal states.
- Add synthetic zero-read and one-read history-gap accounting assertions.
- Do not change runtime behavior, UI output, or existing local artifacts.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `v2-daily-decision-page`: pin existing scan/read accounting with explicit
  regression scenarios; no production behavior change.

## Impact

Only `tests/logic/test_nominal_baseline.py` and OpenSpec documentation. No new
runtime dependencies, schemas, trading semantics, or production writes.
