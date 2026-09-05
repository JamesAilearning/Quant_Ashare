## ADDED Requirements

### Requirement: Aggregate replacement SHALL preserve previously declared coverage

The fetcher SHALL guard selected replacements of existing namechange, suspend_d,
index_weight and trade_cal aggregate files before their data API calls. It SHALL
consume the existing fetch manifest through one previous snapshot per fetch run,
including endpoint records with holes, rather than the hole-free freshness
watermark. The consumed schema version SHALL be an integer of the supported
version; endpoint status SHALL be complete or holes and coverage SHALL contain
ordered, nonempty, real ASCII YYYYMMDD date strings.

The effective replacement interval SHALL contain the previously declared endpoint
coverage. For trade_cal the effective start SHALL be TRADE_CAL_START_DATE, matching
the actual API request, and its prior declared start SHALL be clamped to that
same floor for comparison, since the manifest stores CLI rather than actual
calendar query bounds. A known-range shrink SHALL preserve the target bytes and
record an unsafe_overwrite hole with zero attempts under file or index={code},
as appropriate, without a data API call or written/verified count for that unit.
No request SHALL be silently widened and no old/new row merge SHALL be introduced.

An existing target with unknown or unusable coverage SHALL instead raise
TushareFetcherError, preserving the target and preventing the CLI from writing a
new completed-run manifest. A refusal SHALL NOT generate provenance that permits
the same unknown file's replacement on a subsequent narrow retry. Recovery
guidance SHALL require backup/inspection and a separate staging rebuild rather
than fabricating dates or deleting metadata to bypass the guard.

First writes to absent targets, no-write resume and dry runs SHALL retain existing
behavior. Stock-basic snapshots and ticker-year replacements SHALL retain their
existing separate semantics. Existing manifest/result schemas, response checks,
and later merge/error rules SHALL remain unchanged. This guard SHALL protect
declared coverage without claiming per-file byte provenance, vendor completeness,
or automatic repair of past corruption.

#### Scenario: A narrow refresh cannot shorten a known aggregate

- **WHEN** a selected existing aggregate replacement excludes either end of its
  previously declared coverage, whether the prior endpoint is complete or holey
- **THEN** its bytes remain unchanged, no data call is made, and its stable
  unsafe-overwrite hole has zero attempts and zero written/verified counts
- **AND** a later manifest merge refusal cannot occur after shortening that file

#### Scenario: Unknown provenance cannot authorize itself on retry

- **WHEN** an existing aggregate selected for replacement has missing, corrupt,
  malformed or empty prior coverage and the same CLI request is repeated
- **THEN** each attempt hard-fails before that target's data call or write
- **AND** no new manifest is produced by either failed attempt to legitimize the
  requested narrow interval as that old file's acquisition history

#### Scenario: Safe refresh and no-write paths retain their contracts

- **WHEN** the request covers known prior bounds, including the actual fixed
  calendar start, or a target is absent
- **THEN** existing acquisition, retry, validation and atomic publication apply
- **AND** no-write resume and dry-run paths do not require new provenance reads
  or change existing files
