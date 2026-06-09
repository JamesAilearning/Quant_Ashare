# v2-ashare-survivorship-correction Specification (delta)

## ADDED Requirements

### Requirement: Bundle build SHALL refuse a holey fetch and stamp the bundle's fetch integrity

The qlib bin build SHALL refuse to build from an INCOMPLETE tushare fetch unless
explicitly overridden. Incomplete means ANY of: a HOLEY fetch (the P3-4b
`fetch_manifest.json` records a hole on any endpoint); a MISSING manifest (which
cannot confirm completeness, and which P3-4b deliberately leaves when it
invalidates the manifest on a hard abort); OR a manifest that does not record the
endpoints the builder consumes (`stock_basic` / `daily` / `adj_factor`) as
fetched — a partial `01_fetch_tushare --endpoints …` run has no holes yet never
fetched the OHLCV / adjustment core, so absence-of-holes alone SHALL NOT be read
as complete. On any of these, `QlibBinBuilder.build` SHALL raise
(an explicit `QlibBinBuilderError` naming the reason) and write no bundle, unless
the operator passes `allow_holey_fetch` (`--allow-holey-fetch`) to build a
research / inspection bundle from partial data. Whether the build is clean or
overridden, it SHALL write a fetch-integrity stamp into the bundle (atomically,
promoted with the bins) recording `built_from_holey_fetch` and, when holey, the
recorded holes — so the downstream recommend boundary can gate on it. This build
override is build-only: it SHALL NOT sanction recommending from the bundle.

#### Scenario: a holey fetch refuses the build
- **WHEN** the fetch manifest in the tushare dir records a hole on any endpoint
  and `allow_holey_fetch` is not set
- **THEN** `build()` raises and writes no bundle

#### Scenario: a missing manifest refuses the build
- **WHEN** there is no `fetch_manifest.json` in the tushare dir and
  `allow_holey_fetch` is not set
- **THEN** `build()` raises (completeness cannot be confirmed)

#### Scenario: a partial fetch missing a required endpoint refuses the build
- **WHEN** the manifest has no holes but does not record a required endpoint
  (e.g. only `stock_basic` was fetched, never `daily` / `adj_factor`) and
  `allow_holey_fetch` is not set
- **THEN** `build()` raises — absence of holes is not completeness when the
  bundle's core inputs were never fetched

#### Scenario: a complete fetch builds and stamps clean
- **WHEN** the fetch manifest is present with no holes
- **THEN** the bundle is built and stamped `built_from_holey_fetch = false`

#### Scenario: an overridden holey build is stamped holey with its holes
- **WHEN** the fetch is holey and `allow_holey_fetch` is set
- **THEN** the bundle is built and stamped `built_from_holey_fetch = true` with the
  recorded holes, so the recommend boundary can still refuse it
