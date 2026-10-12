## ADDED Requirements

### Requirement: Recommendation schedules SHALL be explicit provenance-bound Tushare snapshots

A separate manual acquisition command SHALL call the existing Tushare client for SSE `trade_cal` with explicit start/end and fields `exchange,cal_date,is_open`, and SHALL publish one new JSON snapshot only after validating the complete response. The snapshot SHALL carry schema version, source API, exchange, exact query, aware acquisition timestamp and all rows. It SHALL NOT modify raw fetch manifests or the qlib price calendar. Recommendation SHALL read and hash the same bytes without fetching online or deriving weekdays.

#### Scenario: Complete genuine schedule is usable
- **WHEN** a real response contains every requested calendar day exactly once with SSE identities and strict binary integer markers
- **THEN** one provenance-bound snapshot can be published without replacing an existing snapshot
- **AND** its open days can supply a future planned session without future price bars

#### Scenario: Damaged or stale schedule refuses
- **WHEN** rows are missing/duplicated, a date or source/exchange is invalid, the query range is not fully covered, the timestamp is future/naive or older than seven days, or the next open session is unavailable
- **THEN** recommendation SHALL refuse before model loading or feature construction and SHALL NOT guess a date or fall back to retrospective behavior

### Requirement: Closed price sessions SHALL be resolved independently of planned sessions

An aware Shanghai clock with a conservative 15:30 cutoff SHALL define which scheduled sessions have closed. Default recommendation SHALL require the real provider tail to equal the latest such session. Price and schedule sessions SHALL agree across their overlap. An explicit as-of SHALL be an actual price session not later than the latest closed session. The schedule SHALL cover the clock date and the next open day after T. Clock eligibility SHALL NOT be treated as proof that final vendor prices were fetched.

#### Scenario: Before and after close use only closed data
- **WHEN** the clock is before the cutoff on an open day
- **THEN** that day's prices SHALL NOT be eligible as the decision session
- **AND** after the cutoff the provider SHALL contain that session or default recommendation SHALL refuse as behind

#### Scenario: Holidays and missing updates do not silently shift dates
- **WHEN** weekends or holidays lie between T and its following session
- **THEN** entry SHALL use the first subsequent open day in the exact validated schedule
- **AND** a provider behind the latest closed session SHALL refuse rather than pass on the 14-day coarse tolerance
