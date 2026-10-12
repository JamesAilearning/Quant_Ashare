## MODIFIED Requirements

### Requirement: The page SHALL state that entry is an already-closed session

The page SHALL disclose entry meaning according to the explicit artifact version. Legacy v2 and older confirmation artifacts SHALL be historical read-only and SHALL NOT be represented as current future-session candidates. V3 SHALL require the candidate contract, display its actual planned `entry_date`, state that entry tradability is unconfirmed and needs human checks at trading time, and SHALL NOT call it an already-closed/executable session. Current status SHALL require the current model/provider identity, as-of equal to provider tail and generation's latest closed session, and an unexpired entry session under the shared timing contract. Unknown/missing v3 fields SHALL fail closed. V3 candidates SHALL block nominal-holding baseline promotion and SHALL NOT be skipped to reuse older v2 holdings. No presentation or journal action SHALL silently confirm execution.

#### Scenario: V3 future candidate has honest disclosure
- **WHEN** a valid v3 artifact is rendered
- **THEN** the page names its planned entry date and unconfirmed execution conditions
- **AND** it does not promote the list to holdings or claim future prices exist

#### Scenario: Expired or legacy artifact is not current
- **WHEN** the target session has expired, the provider/model binding differs, or the artifact is legacy
- **THEN** the page displays historical/noncurrent status rather than a current candidate claim

#### Scenario: Candidate blocks nominal-holding backtracking
- **WHEN** baseline scanning encounters a v3 candidate
- **THEN** the baseline is explicitly unavailable and scanning SHALL NOT jump past it to promote an older list
