## ADDED Requirements

### Requirement: Aggregate acquisition SHALL refuse observable truncation and history loss

For `suspend_d`, raw acquisition SHALL collect sequential,
non-overlapping calendar-month windows covering exactly the requested interval.
It SHALL bisect potentially saturated windows down to a day, checking raw row
counts before deduplication. An unresolved saturated day SHALL be rejected.
For `namechange`, acquisition SHALL retain its single requested range and reject
potentially saturated responses without a pagination or date-partition fallback.
The named conservative tripwires SHALL be 10,000 name-change rows and 5,000
suspension rows, not represented as guaranteed vendor limits. Named endpoint
budgets SHALL bound partition calls to 2,000, accepted rows to 1,000,000 and
accepted DataFrame deep memory to 256 MiB; an exceeded budget SHALL fail without
partial publication. Existing per-call bounded retries and rate limiting remain.

Every response SHALL have the requested schema and real non-null query dates
within its requested window: `ann_date` for name changes and `trade_date` for
suspensions. A valid empty window SHALL be allowed; missing schema, missing
suspension dates, malformed dates or out-of-window records SHALL be rejected rather
than filtered, guessed or silently repaired. Name-change effective `start_date`
SHALL NOT be substituted for announcement date filtering.
Nullable announcement dates and end dates SHALL remain nullable. The name-change
path SHALL NOT claim completeness of never-observed history without announcement
dates merely because a range query passed these safeguards.

Before the sole atomic publication, every retained business key and every
business key observed in a saturated parent response SHALL remain in the
candidate. Suspension keys SHALL contain all four requested fields. Name-change
keys SHALL contain all requested fields except `end_date`, explicitly allowing
only an end-date correction for an otherwise identical record. Exact duplicate
rows SHALL be deduplicated; conflicting payloads for one key SHALL be rejected.
Old records SHALL NOT be unioned into a partial candidate to make it appear
complete. Malformed retained sources SHALL fail explicitly.

The existing skip, dry-run and manifest-provenance/range protections SHALL remain.
On response, preservation or budget failure the producer SHALL retain the old
file byte-for-byte, report zero writes and a stable `file` hole with reason
`unusable_response`, and SHALL NOT advance retained manifest coverage. A complete
successful retry SHALL heal that hole. Existing retryable and non-retryable
network-error semantics SHALL remain unchanged. Other endpoints, raw schemas,
manifest/DTO schemas, provider publication and runtime trading SHALL not change.
These checks SHALL NOT be described as proof of all unobserved vendor history.

#### Scenario: A wider request returns a different historical slice
- **WHEN** retained suspension records from 2018 are absent from a candidate
  containing only 2015 records despite covering request bounds
- **THEN** the old file remains byte-identical, the endpoint writes zero files,
  and its stable file hole prevents the run from being called complete

#### Scenario: A raw response reaches the safety tripwire
- **WHEN** a response contains at least the endpoint tripwire number of rows,
  even if most rows are exact duplicates
- **THEN** suspension acquisition subdivides the interval or rejects a saturated
  single day, name-change acquisition rejects the response, and neither path
  publishes a saturated response as a complete file

#### Scenario: Partitions cover real dates exactly
- **WHEN** the interval spans partial months, a year boundary or February 29
- **THEN** successful leaf windows cover each requested date once without gaps,
  overlaps or automatic widening

#### Scenario: Announcement dates differ from effective dates
- **WHEN** a name-change response has a valid in-window announcement date and
  a different real effective date
- **THEN** acquisition uses the announcement date for window validation
- **AND** a missing announcement date remains missing, not filled from effective date

#### Scenario: A final window fails or collection exceeds its budget
- **WHEN** earlier windows succeeded but a later response is unusable, exhausts
  retries, drops an observed parent key or exceeds a named budget
- **THEN** no aggregate is published and prior file/coverage remain intact

#### Scenario: A name change receives a legitimate end-date correction
- **WHEN** every old key remains and the only changed payload is `end_date`
- **THEN** the fresh candidate is published with the new end date
- **AND** a different name on the same effective date remains a separate record

#### Scenario: Empty or conflicting candidates cannot erase history
- **WHEN** a candidate is empty while retained business history is nonempty,
  or one name-change key has multiple different end dates
- **THEN** the candidate is rejected without changing the old file

#### Scenario: A retry really heals a stable file hole
- **WHEN** an incomplete attempt is followed by a fully collected candidate
  that preserves old and observed keys
- **THEN** only the successful attempt publishes and heals the file hole;
  failed zero-write attempts cannot advance coverage or wash away the hole
