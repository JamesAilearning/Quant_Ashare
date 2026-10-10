## ADDED Requirements

### Requirement: The existing single-stock policy SHALL recognize only the exact original pair as additional audit evidence

Under explicit `suspend-688005-20260116-conflict`, reference incident keys SHALL be exactly the existing R key or the existing R+S pair, and retained incident keys SHALL be exactly R, S or R+S. The candidate incident keys MUST remain exactly the existing S key. Every other stock/date/timing/type and every legacy/combined policy boundary MUST remain unchanged.

#### Scenario: Real paired original source produces scoped evidence
- **WHEN** an unquarantined original contains the exact R+S pair and a complete unrelated history, and the real candidate contains exact S while preserving unrelated history
- **THEN** publication produces the existing single-stock quarantine for missing R on 20260116, preserves complete original bytes and does not claim clean acquisition

#### Scenario: Changed or additional candidate cannot use pair support
- **WHEN** candidate incident keys are empty, R-only, R+S, retimed, changed type or extra keys after a conflict
- **THEN** publication refuses before changing raw or evidence, without widening missing-date authorization

#### Scenario: Unrelated history is never incident-authorized
- **WHEN** the candidate loses any unrelated original/retained key, including any of the eight SH688766 records
- **THEN** the unchanged generic retention guard refuses without accepted publication

### Requirement: Whole paired source evidence SHALL survive repeated publication and recovery

The existing seven-field evidence and journal schemas MUST remain unchanged. Reference hashes SHALL bind the whole original paired file, including both contradictory records; publication, provider verification and journal recovery MUST revalidate actual evidence bytes. Repeat exact S responses SHALL remain quarantined and MUST NOT silently restore eligibility or certify quarantined historical evaluation.

#### Scenario: Repeat publication keeps the original pair
- **WHEN** matching prior evidence is verified and the next candidate remains exact S
- **THEN** the original paired reference SHA remains unchanged and provider gates accept only the matching explicit single policy

#### Scenario: Interrupted first publication restores the genuine paired original
- **WHEN** the existing matching-policy publication journal is recovered before a manifest commits
- **THEN** the complete original paired bytes are restored, the pending lineage remains guarded and a matching reviewed successor can complete without synthetic reference data

#### Scenario: Evidence or policy is changed
- **WHEN** paired reference bytes, prior evidence, journal policy or authorization is tampered or mismatched
- **THEN** existing verification refuses and preserves failure evidence without silently falling back

### Requirement: Pair support SHALL NOT alter existing clean or legacy behavior

The no-prior complete R-only acquisition SHALL remain clean under the existing rules. Established conflicts MUST still refuse restored/changed candidate history. Default/no-opt-in, legacy and combined policy behavior, serving identity, model, scoring, holdings and transaction mechanisms MUST remain unchanged.

#### Scenario: Existing clean and other-policy paths remain unchanged
- **WHEN** an existing R-only clean first acquisition or legacy/combined policy input is processed
- **THEN** its prior acceptance/refusal, evidence schema and operator-visible meaning are preserved
