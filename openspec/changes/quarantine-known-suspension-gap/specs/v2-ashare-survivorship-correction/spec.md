## MODIFIED Requirements

### Requirement: Bundle build SHALL refuse a holey fetch and stamp the bundle's fetch integrity

The qlib bin build SHALL refuse to build from an INCOMPLETE tushare fetch unless
explicitly overridden. Incomplete means ANY of: a HOLEY fetch (the P3-4b
`fetch_manifest.json` records a hole on any endpoint); a MISSING manifest (which
cannot confirm completeness, and which P3-4b deliberately leaves when it
invalidates the manifest on a hard abort); OR a manifest that does not record the
endpoints the builder consumes (`stock_basic` / `daily` / `adj_factor`) as fetched
WITH ESTABLISHED (non-empty) coverage — a required endpoint that is absent, OR
present but recorded with EMPTY coverage (a partial `01_fetch_tushare --endpoints …`
run, or a first manifest skipped over a pre-existing dump, which P3-4b records with
empty coverage precisely so this gate can catch it), has no confirmed fetch, so
absence-of-holes alone SHALL NOT be read as complete. On any of these,
`QlibBinBuilder.build` SHALL raise
(an explicit `QlibBinBuilderError` naming the reason) and write no bundle, unless
the operator passes `allow_holey_fetch` (`--allow-holey-fetch`) to build a
research / inspection bundle from partial data, or explicitly selects the
`scoped-suspension-quarantine` policy. The scoped policy SHALL require complete
core coverage and exactly its verified incident hole, including raw evidence and
candidate hash checks. It SHALL NOT authorize missing prerequisites or any other
hole. Whether the build is clean or overridden, it SHALL write a fetch-integrity
stamp into the bundle (atomically, promoted with the bins) recording
`built_from_holey_fetch` and, when holey, the recorded holes including any
structured quarantine evidence. A qualified build remains holey. Both build
overrides are build-only: neither SHALL sanction recommending from the bundle.

#### Scenario: a holey fetch refuses the build
- **WHEN** the fetch manifest records a hole, `allow_holey_fetch` is not set and
  no separately selected scoped policy verifies it
- **THEN** `build()` raises and writes no bundle

#### Scenario: a missing manifest refuses the build
- **WHEN** there is no `fetch_manifest.json` and `allow_holey_fetch` is not set
- **THEN** `build()` raises, including when the scoped policy is selected

#### Scenario: a partial fetch missing a required endpoint refuses the build
- **WHEN** a required endpoint is absent or has EMPTY coverage and
  `allow_holey_fetch` is not set
- **THEN** `build()` raises, including when the scoped policy is selected

#### Scenario: a corrupt manifest fails loud as a builder error
- **WHEN** the manifest is unreadable, unknown-schema or has invalid quarantine metadata
- **THEN** `build()` raises `QlibBinBuilderError` regardless of either override

#### Scenario: a complete fetch builds and stamps clean
- **WHEN** the fetch manifest is present with no holes and required coverage
- **THEN** the bundle is built and stamped `built_from_holey_fetch = false`

#### Scenario: an overridden holey build is stamped holey with its holes
- **WHEN** the fetch is holey and `allow_holey_fetch` is set
- **THEN** the bundle is built and stamped `built_from_holey_fetch = true` with
  recorded holes; structured quarantine still requires its own validation

#### Scenario: a verified scoped quarantine builds without blanket permission
- **WHEN** only the approved quarantine remains, its evidence verifies, core
  coverage is complete and the matching scoped policy is selected
- **THEN** build proceeds, preserves holes and quarantine evidence in its stamp,
  and does not implicitly authorize serving or historical certification
