## ADDED Requirements

### Requirement: Closed combined incident authorization
The system SHALL accept only explicit `suspend-688005-688766-observed-20261008` for the approved combination and MUST preserve existing policy semantics and default refusal.

#### Scenario: Exact live-observed combination
- **WHEN** reference affected keys are the eight established 688766 omissions plus 688005/20260116/null/R, and candidate affected keys are exactly 688005/20260116/null/R plus 688005/20260116/09:30-09:30/S
- **THEN** publication SHALL retain source bytes, emit the seven-field combined evidence with exactly eight missing dates, and keep the fetch result incomplete

#### Scenario: Unapproved loss or payload
- **WHEN** another retained key disappears, an affected-date key changes, any legacy key returns, R or S disappears, or reference does not contain the original nine exact keys
- **THEN** publication MUST refuse without replacing retained raw data

#### Scenario: Repeat or interrupted publication
- **WHEN** the same payload repeats or publication is interrupted
- **THEN** matching-policy validation/recovery SHALL preserve original reference and enforce durable pending refusal; cross-policy recovery MUST refuse without mutating evidence

### Requirement: Exact evidence and query coverage
Combined evidence MUST use the exact eight legacy missing dates and MUST bind a query covering every original incident date including 20260116. Byte/hash/schema/partition/retention and one-hole checks MUST remain enforced.

#### Scenario: Truncated conflict coverage or mixed holes
- **WHEN** query ends before 20260116, evidence carries a subset/extra missing date, a hash disagrees, or another hole exists
- **THEN** the corresponding contract or consumer gate MUST refuse

### Requirement: Two-security serving disclosure and isolation
Explicitly authorized daily serving SHALL exclude exactly SH688005 and SH688766 before Top-K without rewriting scores or existing holdings. Combined JSON SHALL disclose the exact ordered instrument list; CSV SHALL disclose its semicolon-delimited projection. Old single-security artifact shapes MUST remain unchanged.

#### Scenario: Two high-scoring excluded securities
- **WHEN** either or both affected securities score above other candidates
- **THEN** neither SHALL enter picks, original scores SHALL remain in audit, and exclusion counts SHALL match the real scored rows

#### Scenario: No affected scored securities
- **WHEN** neither security occurs in scores
- **THEN** serving SHALL retain active incomplete-data disclosure with zero exclusions

#### Scenario: Corrupt metadata or leaked pick
- **WHEN** instrument list is missing, extra, reordered, singular, cross-policy, or either security occurs in picks
- **THEN** export MUST refuse before I/O and read-only UI MUST refuse presenting it as actionable

### Requirement: No certification or implicit authority
The policy MUST NOT authorize historical performance evaluation, Pipeline/WalkForward execution, general hole bypass, UI opt-in or automatic production deployment.

#### Scenario: Historical or unauthorized daily request
- **WHEN** historical execution uses this bundle or daily serving omits/mismatches the explicit ID even with a generic hole override
- **THEN** the request MUST refuse before model/metric work or output publication
