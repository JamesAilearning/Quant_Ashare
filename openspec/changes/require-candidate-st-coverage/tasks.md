## 1. Regression evidence

- [x] 1.1 Add synthetic recommend-path tests for missing, invalid and duplicate required names; record failures before implementing the guard.
- [x] 1.2 Cover masked/NaN/unrelated exemptions, valid ST ranking and reason/count preservation, and mandatory whole-snapshot failure with an all-masked pool.

## 2. Minimal implementation

- [x] 2.1 Validate required original name rows from the same snapshot before name-map coercion and Top-K, without changing signatures or persisted schemas.

## 3. Review and verification

- [x] 3.1 Complete fresh independent local reviews and address all P0/P1/P2 findings.
- [x] 3.2 Run targeted and full logic/governance tests serially, touched source import, pinned lint/type checks, and strict OpenSpec validation; inspect staged diff before publishing.
