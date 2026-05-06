## 1. Contract and Configuration

- [x] 1.1 Add a Tushare qlib provider bundle config model that rejects token fields, requires output paths, date range, universe scope, and supported `data_adjust_mode`.
- [x] 1.2 Add manifest and validation-profile dataclasses for generated provider bundles, including source APIs, coverage, row counts, adjustment mode, validation health, and publisher version.
- [x] 1.3 Add contract/governance tests proving Tushare provider publishing remains opt-in and does not change default `provider_uri` or canonical qlib init semantics.

## 2. Tushare Fetch and Staging

- [x] 2.1 Extend the Tushare data client boundary or add typed fetch helpers for `daily`, `adj_factor`, `trade_cal`, and stock metadata calls without import-time Tushare dependency.
- [x] 2.2 Implement a staging layer that writes raw Tushare payloads to a deterministic temporary/staging directory before final bundle publication.
- [x] 2.3 Add resumable/idempotent staging behavior for repeated runs over the same date and instrument scope.

## 3. Validation and Adjustment

- [x] 3.1 Validate required columns, parseable dates, duplicate instrument-date rows, instrument coverage, date coverage, and calendar alignment before conversion.
- [x] 3.2 Validate adjustment-factor coverage for adjusted output and fail loudly when factors are missing or unsupported adjustment modes are requested.
- [x] 3.3 Implement explicit unadjusted/pre-adjusted/post-adjusted output transformations and record the selected mode in the manifest.
- [x] 3.4 Add logic tests for malformed staged data, missing factors, calendar mismatches, empty coverage, and unsupported adjustment modes.

## 4. Qlib Bundle Publishing

- [x] 4.1 Implement the publisher that converts validated staged data into a qlib-compatible provider bundle in an isolated output directory.
- [x] 4.2 Publish the final bundle atomically so failed validation or conversion leaves any previous bundle unchanged.
- [x] 4.3 Write a sidecar manifest and validation profile for every successful publish, excluding Tushare secrets.
- [x] 4.4 Add a small fixture-based conversion test proving qlib can initialize against the generated provider layout.

## 5. CLI, Examples, and Comparison

- [x] 5.1 Add a shipped CLI and example config for building a Tushare qlib provider bundle without storing the token in YAML.
- [x] 5.2 Add optional comparison reporting against an existing qlib provider path, covering row counts, coverage overlap, missing instruments, and price/volume deltas.
- [x] 5.3 Update README or docs to explain that Tushare OHLCV training is opt-in and requires explicitly pointing `provider_uri` at the generated bundle.

## 6. Verification

- [x] 6.1 Run targeted logic/governance tests for the new contract, publisher, CLI, and comparison behavior.
- [x] 6.2 Run `openspec validate add-tushare-qlib-provider-bundle --strict`.
- [x] 6.3 Run `openspec validate --all --strict`.
- [x] 6.4 Perform a scope-drift review confirming no default training source or official metric semantics changed.
