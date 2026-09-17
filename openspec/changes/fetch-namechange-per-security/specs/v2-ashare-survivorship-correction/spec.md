## ADDED Requirements

### Requirement: Full per-security name history SHALL be explicitly selected and atomically published

Fetcher and daily-update configuration SHALL accept only `date_range` (compatible
default) and `per_security_full` for `namechange_mode`. Both CLI entry points SHALL
expose `--namechange-mode`; daily-update SHALL pass the selected non-default mode
to the existing fetch producer. Invalid modes and invalid full-mode operational
envelope dates SHALL fail at configuration before mutation. Neither
strategy SHALL fall back to the other. The existing date-range and suspension
contracts SHALL otherwise remain unchanged.

Full mode SHALL query the sorted union of the two stock-basic snapshots and
retained name-history codes. Before name API calls it SHALL require readable,
schema-valid L/D snapshots with unique valid SH/SZ/BJ codes, no cross-bucket
overlap, nonempty L and D buckets, fewer than 6,000 rows per snapshot and the
configured run's embedded snapshot date. Prior stock-basic provenance SHALL be
complete and hole-free unless both buckets were successfully refreshed in this
fetcher run; current stock-basic holes SHALL block full-mode publication. Missing
or corrupt prerequisites SHALL NOT be treated as an empty security set.

Every security SHALL be queried serially with `ts_code` and the existing fields,
without date filters, pagination or code remapping. Raw history SHALL NOT be
locally date-filtered or have missing announcement dates filled. Every response
SHALL match the requested code and existing schema/date/null rules. The frozen
set SHALL contain at most 10,000 securities/calls; each raw response SHALL remain
below the 10,000-row safety tripwire before deduplication. Existing cumulative
1,000,000-row and 256 MiB accepted deep-memory budgets SHALL apply. Exceeding any
limit SHALL fail without partial publication or truncation.

Retained history SHALL be validated before requests. Only exact duplicate rows
SHALL be removed; all retained business keys SHALL survive, and only `end_date`
payload corrections SHALL be allowed. Old rows SHALL NOT be unioned into the
candidate. The existing atomic aggregate producer SHALL publish once, only after
every selected security succeeds. Failure SHALL preserve prior file bytes and
coverage; retryable errors SHALL retain their classification and response defects
SHALL use the stable `file` unusable-response hole. Auth/parameter errors and
missing/corrupt prerequisite provenance SHALL still hard-abort.

Full mode SHALL preserve existing range/provenance guards. Its v1 manifest
interval SHALL remain the requested operational envelope, not output min/max,
a mode or all-security completeness certificate, or historical PIT knowledge.
A would-be blind skip of an existing file in full mode SHALL require explicit
refresh instead; a forced file-hole retry SHALL acquire all codes. Dry-run SHALL
remain non-mutating. No raw/manifest schema, official metrics, model, selection,
provider publication or scheduled task SHALL change as a side effect.

#### Scenario: Missing announcements survive full acquisition
- **WHEN** a selected code returns old history outside the operational envelope
  and rows with null announcement dates
- **THEN** all valid rows remain unchanged and no API or local date filter is used

#### Scenario: Historical code is absent from current snapshots
- **WHEN** a retained name-history code appears in neither current L nor D bucket
- **THEN** the frozen set still includes and queries that code exactly once

#### Scenario: Stock-basic refresh failed but yesterday's files exist
- **WHEN** a stock-basic prerequisite is stale, missing, malformed, saturated,
  or has an unresolved hole
- **THEN** no full name candidate is published and no old file establishes success

#### Scenario: A late response fails or claims another code
- **WHEN** earlier codes succeeded but a later query exhausts retries, violates
  schema/identity, reaches a response cap or exceeds the cumulative budget
- **THEN** the old aggregate and coverage remain unchanged with a failed file unit

#### Scenario: Empty response cannot erase an observed code
- **WHEN** a valid empty response would remove a retained business key
- **THEN** the full candidate is rejected, without old-row fallback

#### Scenario: Duplicate or conflicting records
- **WHEN** exact duplicates or conflicting end dates arrive
- **THEN** only exact duplicates are collapsed; conflicting payloads fail

#### Scenario: Mode migration cannot masquerade as resume
- **WHEN** full mode is requested for an existing file without refresh or a forced retry
- **THEN** it refuses before name API work and requests explicit refresh
- **AND** legacy date-range resume and all dry-run behavior remain compatible

#### Scenario: Successful complete retry
- **WHEN** all frozen codes succeed and all old business keys remain
- **THEN** one atomic write heals the stable file hole and records only the
  requested envelope, not inferred earliest source dates

#### Scenario: Invalid mode reaches a public entry point
- **WHEN** either config or CLI receives an unknown mode
- **THEN** it rejects the input before client construction, manifest reset,
  lock/status writes or data operations
