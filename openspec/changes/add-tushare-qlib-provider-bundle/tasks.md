## 1. Contract and Configuration

- [ ] 1.1 Add a Tushare qlib provider bundle config model that rejects token fields, requires output paths, date range, universe scope, and supported `data_adjust_mode`.
- [ ] 1.2 Add manifest and validation-profile dataclasses for generated provider bundles, including source APIs, coverage, row counts, adjustment mode, validation health, and publisher version.
- [ ] 1.3 Add contract/governance tests proving Tushare provider publishing remains opt-in and does not change default `provider_uri` or canonical qlib init semantics.

## 2. Tushare Fetch and Staging

- [ ] 2.1 Extend the Tushare data client boundary or add typed fetch helpers for `daily`, `adj_factor`, `trade_cal`, and stock metadata calls without import-time Tushare dependency.
- [ ] 2.2 Implement a staging layer that writes raw Tushare payloads to a deterministic temporary/staging directory before final bundle publication.
- [ ] 2.3 Add resumable/idempotent staging behavior for repeated runs over the same date and instrument scope.

## 3. Validation and Adjustment

- [ ] 3.1 Validate required columns, parseable dates, duplicate instrument-date rows, instrument coverage, date coverage, and calendar alignment before conversion.
- [ ] 3.2 Validate adjustment-factor coverage for adjusted output and fail loudly when factors are missing or unsupported adjustment modes are requested.
- [ ] 3.3 Implement explicit unadjusted/pre-adjusted/post-adjusted output transformations and record the selected mode in the manifest.
- [ ] 3.4 Add logic tests for malformed staged data, missing factors, calendar mismatches, empty coverage, and unsupported adjustment modes.

## 4. Qlib Bundle Publishing

- [ ] 4.1 Implement the publisher that converts validated staged data into a qlib-compatible provider bundle in an isolated output directory.
- [ ] 4.2 Publish the final bundle atomically so failed validation or conversion leaves any previous bundle unchanged.
- [ ] 4.3 Write a sidecar manifest and validation profile for every successful publish, excluding Tushare secrets.
- [ ] 4.4 Add a small fixture-based conversion test proving qlib can initialize against the generated provider layout.

## 5. CLI, Examples, and Comparison

- [ ] 5.1 Add a shipped CLI and example config for building a Tushare qlib provider bundle without storing the token in YAML.
- [ ] 5.2 Add optional comparison reporting against an existing qlib provider path, covering row counts, coverage overlap, missing instruments, and price/volume deltas.
- [ ] 5.3 Update README or docs to explain that Tushare OHLCV training is opt-in and requires explicitly pointing `provider_uri` at the generated bundle.

## 6. Verification

- [ ] 6.1 Run targeted logic/governance tests for the new contract, publisher, CLI, and comparison behavior.
- [ ] 6.2 Run `openspec validate add-tushare-qlib-provider-bundle --strict`.
- [ ] 6.3 Run `openspec validate --all --strict`.
- [ ] 6.4 Perform a scope-drift review confirming no default training source or official metric semantics changed.
