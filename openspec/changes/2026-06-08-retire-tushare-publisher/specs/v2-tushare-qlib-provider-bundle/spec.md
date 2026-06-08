# v2-tushare-qlib-provider-bundle Specification (delta)

The entire Tushare "publisher" qlib-provider-bundle capability is retired (unify
U3). It was a non-production second builder; production bundles are built solely
by the data-pipeline scripts (`src/data/pit/qlib_bin_builder.py`). All
requirements below are removed.

**Migration** (applies to every removed requirement): build production qlib
bundles via `scripts/data_pipeline/` (`01_fetch_tushare` → `05_build_qlib_bins`,
i.e. `QlibBinBuilder`), and point training / inference / walk-forward at the
result through `QUANT_PROVIDER_URI` (ops Phase 1). There is no operator-UI
Tushare ingest path any more.

## REMOVED Requirements

### Requirement: Tushare provider publishing SHALL remain opt-in

**Reason**: The publisher it gated was retired (unify U3); there is no publisher to opt into.

### Requirement: Tushare OHLCV publishing SHALL use explicit source APIs and secrets boundaries

**Reason**: Retired with the publisher (unify U3) — no UI ingest reads the Tushare token any more.

### Requirement: Generated qlib bundles SHALL declare adjustment semantics explicitly

**Reason**: Retired with the publisher (unify U3); the production builder writes a single PIT bundle with one adjustment basis — PRE-ADJUSTED prices (`close × adj_factor`), per `qlib_bin_builder`'s documented adjusted-price contract (absolute adjusted prices are not PIT-correct features; consumers use within-ticker ratios).

### Requirement: Generated qlib bundles SHALL support explicitly configured benchmark indexes

**Reason**: Retired with the publisher (unify U3); benchmark series are published by the data-pipeline benchmark publisher, not this builder.

### Requirement: Publisher validation SHALL reject malformed staged market data

**Reason**: Retired with the publisher (unify U3); the production builder's own validation (`06_validate_pit_data` + the adj_factor guard) governs the production bundle.

### Requirement: Publisher SHALL preserve raw staged market payloads across instrument scopes

**Reason**: Retired with the publisher (unify U3); there is no staging layer.

### Requirement: Generated bundles SHALL include provenance and validation manifests

**Reason**: Retired with the publisher (unify U3). A thin "validate + inspect the production bundle" view is deferred to Phase 3 P3-6.

### Requirement: Publisher SHALL avoid partial publication on failure

**Reason**: Retired with the publisher (unify U3); the production builder has its own atomic-rename + cleanup-on-failure path.

### Requirement: Tushare provider comparison SHALL be informational

**Reason**: Retired with the publisher (unify U3); the comparison report module is deleted.

### Requirement: Tushare VWAP conversion SHALL apply adjustment factors exactly once

**Reason**: Retired with the publisher (unify U3); the production builder does not emit a `vwap` bin (factor mining derives vwap from money/volume).

### Requirement: Tushare client SHALL reuse its pro_api handle

**Reason**: Retired with the publisher (unify U3); the publisher's fetcher is deleted (the data-pipeline fetcher is unaffected).

### Requirement: Publisher validation SHALL reject non-finite staged OHLCV values

**Reason**: Retired with the publisher (unify U3); non-finite/≤0 adj_factor on the production bundle is rejected by `QlibBinBuilder._validate_adj_factor` (P3-1).
