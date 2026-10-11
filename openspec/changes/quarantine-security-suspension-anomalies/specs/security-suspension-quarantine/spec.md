## ADDED Requirements

### Requirement: Stable per-security authorization SHALL preserve legacy exact policies

The system SHALL support the explicit `suspend-security-anomalies-v1` policy with immutable typed evidence containing the existing seven fields, required sorted unique `quarantined_ts_codes`, and required sorted unique `history_sha256` (empty initially). Existing exact policies SHALL retain their original seven-field schema and behavior. Absent, unknown or mismatched authorization SHALL NOT admit dynamic quarantine.

#### Scenario: New attributable loss does not require a new incident ID
- **WHEN** a complete valid suspension response loses original keys of two individually identified securities under the new opt-in
- **THEN** one typed suspend_d/file/quarantined_history hole carries their evidence-derived identities
- **AND** old exact policies and default callers still refuse this new shape

### Requirement: Isolation SHALL be proven by bounded complete raw evidence

Quarantine SHALL be derived from actual original-reference, retained or immutable prior-witness four-field key loss, with exact code/date agreement at independent verification gates and actual raw SHA256 bindings. It SHALL cover at most ten securities and strictly less than half of the securities in the final persisted evidence baseline (original/current-retained/necessary witnesses). Pruned witnesses SHALL NOT inflate this denominator. The full query SHALL cover all retained/reference/witness dates. Empty, malformed, truncated, over-budget, invalid-code, API-failed, mixed-endpoint or systemic responses SHALL remain failures. Candidate rows SHALL remain exactly the validated vendor rows, never merged with old history.

#### Scenario: Original pair and unrelated omissions are preserved as evidence
- **WHEN** the original includes the genuine SH688005 R+S pair plus eight SH688766 keys and the new candidate retains only S for those incidents
- **THEN** both securities are quarantined with exactly nine missing dates
- **AND** complete original bytes remain preserved while the candidate is published unchanged

#### Scenario: Additions are not silently declared corrupt
- **WHEN** a candidate retains every reference/retained key and adds valid historical records or another same-date suspension type
- **THEN** additions alone do not authorize quarantine

#### Scenario: Systemic loss or deficient query cannot be waived
- **WHEN** affected securities exceed the count/minority ceiling or the query excludes any original/retained/witness history
- **THEN** publication fails without changing retained raw bytes

### Requirement: Repeated publication SHALL preserve lifecycle and crash closure

Repeated acquisition SHALL reverify prior evidence and retain the original byte reference. Newly attributable losses SHALL extend the evidence-derived list within the same bounds. SHA-bound immutable earlier retained files SHALL remain witnesses while necessary to prove still-missing keys not represented by the original/current-retained files, with at most 32 witnesses and cumulative compressed/decoded budgets. A later complete validated acquisition SHALL remove a security only if every original, immediately retained and required witness key for it is restored. Pending or corrupt transactions SHALL remain refused by ordinary readers and recover only through matching explicit refresh.

#### Scenario: Later-added missing history cannot be forgotten by another update
- **WHEN** a valid acquisition adds X history, a later candidate loses X, and the next candidate still lacks X
- **THEN** X remains provably quarantined through preserved actual retained-file witnesses until the vendor truly restores all its keys

#### Scenario: Later attributable loss does not require another incident policy
- **WHEN** a later valid candidate loses another original security's keys within the bounded minority
- **THEN** the same explicit policy carries the recomputed combined list and original reference hash

#### Scenario: Actual full restoration clears only restored security
- **WHEN** every original, retained and required witness key for one quarantined security returns but another remains missing
- **THEN** the verified current list removes only the restored security and retains the incomplete provenance

### Requirement: Serving SHALL exclude the evidence-derived list without certifying history

Recommendation SHALL exclude every quarantined security before Top-K and validate the identical list in persisted audit/JSON/CSV and existing CLI/read-only disclosures, including zero scored matches. Provider/PIT SHALL retain incomplete provenance. Historical evaluation SHALL refuse dynamic quarantine. No exclusion SHALL imply sale of existing holdings, training/model changes or production acceptance.

#### Scenario: Highest ranked quarantined securities cannot be recommended
- **WHEN** two dynamic quarantined securities have the highest scores
- **THEN** neither appears in picks and eligible securities fill Top-K instead
- **AND** their audit reason is data_quarantine with matching evidence/counts

#### Scenario: Forged identity or historical certification is refused
- **WHEN** an output substitutes another quarantine identity or a historical evaluation consumes the quarantined provider
- **THEN** the relevant gate refuses rather than treating the artifact as clean
