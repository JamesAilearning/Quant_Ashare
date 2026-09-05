## ADDED Requirements

### Requirement: Index weights SHALL use bounded monthly acquisition

The fetcher SHALL request `index_weight` separately for each calendar month
intersecting the inclusive configured range, clipping the first and last month
to that range. It SHALL retain the existing single parquet file per index and
existing serial rate limiting. Invalid calendar dates SHALL raise
`TushareFetcherError` before any index-weight API call or file replacement.
A response containing at least 6,000 rows SHALL
be rejected as potentially truncated with reason class `unusable_response`.
This safety threshold SHALL NOT be presented as a proof of upstream completeness.

Publication SHALL remain atomic for the whole index: any failed or saturated
month SHALL record the stable hole `index={code}` and SHALL NOT create or replace
that index's file with a partial concatenation. Other configured indices SHALL
still be attempted. A successful full run SHALL concatenate the monthly results;
an all-empty run SHALL retain the existing empty-placeholder behavior. Cached
files SHALL retain existing resume and refresh-current semantics; upgrading the
fetcher SHALL NOT certify or silently repair cached legacy files.

#### Scenario: annual CSI800 requests would be truncated
- **WHEN** a full year contains twelve 800-row snapshots and a broad upstream
  query would return only its last 7,000 rows
- **THEN** the fetcher makes twelve monthly calls and publishes all 9,600 rows

#### Scenario: date windows cover a leap month and year boundary
- **WHEN** the configured range crosses December, January and leap-year February
- **THEN** the monthly calls exactly partition that inclusive range without gaps,
  overlaps, or dates beyond the requested endpoints

#### Scenario: a later month fails or reaches the safety threshold
- **WHEN** an earlier month succeeded but a later month fails or returns at least
  6,000 rows
- **THEN** the fetcher records one stable per-index hole, leaves any previous
  file byte-for-byte intact, and publishes no partial new file
- **AND** the remaining configured indices can still complete

#### Scenario: an invalid date must not become an empty successful fetch
- **WHEN** a request includes an impossible date such as February 30 or month 13
- **THEN** acquisition fails loudly before touching any index-weight file,
  including when a previous hole forces an existing file to be retried

#### Scenario: cached legacy history requires explicit repair
- **WHEN** a legacy index-weight file exists with no prior recorded hole
- **THEN** ordinary resume and refresh-current still skip it
- **AND** the operator must explicitly stage a full-range re-fetch and validate
  it before replacing production data
