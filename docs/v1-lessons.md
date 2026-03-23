# V1 Lessons to Reuse in V2

## Keep

- Canonical official metrics path with explicit governance boundaries.
- Decision-first feasibility process before moving constraints into canonical path.
- Regression tests for canonical vs experimental boundaries.
- Data-contract hardening with manifest/provenance metadata.
- Operator-facing status surfaces with compact warning/error summaries.

## Avoid

- Implicit fallback behavior without clear labels.
- Hidden coupling in app runtime initialization.
- Duplicate helper definitions and schema drift.
- Encoding-corrupted operator-facing text.

## V2 Translation

- Define contracts before implementation.
- Keep each change minimal and archivable.
- Prefer auditable semantics over convenience shortcuts.
