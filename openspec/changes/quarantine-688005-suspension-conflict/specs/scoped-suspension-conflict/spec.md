## ADDED Requirements

### Requirement: Exact conflict authorization SHALL be independent of legacy omission

The system SHALL accept `suspend-688005-20260116-conflict` only as a separately selected policy. It SHALL require the original `(688005.SH, 20260116, null, R)` reference and exact `(688005.SH, 20260116, 09:30-09:30, S)` candidate at that incident date. It MUST preserve vendor rows without invention, retain byte-bound evidence, and mark the data incomplete. All other business keys MUST pass ordinary retention validation. The legacy policy MUST retain its original behavior and cannot authorize this conflict.

#### Scenario: Approved exact conflict
- **WHEN** the selected new policy, original reference and exact candidate verify with no other loss
- **THEN** the candidate is published with quarantine evidence and fetch exit 3, never a clean fetch claim

#### Scenario: Other loss or incident payload
- **WHEN** another key is missing, or the candidate has no incident row, a different timing/type, or additional incident rows
- **THEN** acquisition refuses and preserves retained bytes

#### Scenario: No observed conflict on first acquisition
- **WHEN** no prior quarantine exists and the candidate retains the R reference with no conflicting incident row and no other lost key
- **THEN** the existing clean acquisition path is allowed without fabricating quarantine

### Requirement: Conflict evidence and recovery SHALL remain policy bound

All prior evidence, persisted journals and opt-ins MUST select the same incident. Interrupted publication SHALL retain the existing fail-closed recovery and actual-refresh obligation. Repeated exact S responses SHALL preserve the original R reference. Once quarantined, any incident change including a return to R SHALL refuse rather than automatically clear the conflict.

#### Scenario: Repeat exact response
- **WHEN** the retained file already contains S and the next verified candidate has the same incident payload
- **THEN** the original R evidence and incomplete status persist

#### Scenario: Restored or changed response
- **WHEN** an established conflict is followed by R, no incident row or an unapproved payload
- **THEN** refresh refuses without clearing quarantine or altering retained bytes

#### Scenario: Cross-policy recovery
- **WHEN** a prior artifact or journal belongs to the other policy
- **THEN** the selected refresh refuses before mutation

### Requirement: Serving SHALL isolate only the evidence-selected security

Daily serving SHALL require a matching explicit policy and exclude SH688005 before Top-K for the new conflict, preserving scores, audit rows, holdings and historical universes. JSON, CSV, CLI and read-only UI SHALL disclose the selected incident and incomplete data. Outer policy/instrument and inner evidence MUST agree. Historical evaluation SHALL continue to refuse quarantined bundles in both canonical engines. No UI update/recommend button SHALL gain automatic authorization.

#### Scenario: New incident has highest score
- **WHEN** SH688005 ranks first under the new policy
- **THEN** it is excluded, the next eligible security fills Top-K, SH688766 is not excluded by this policy, and audit/disclosure name SH688005

#### Scenario: Mixed evidence identities
- **WHEN** export or UI metadata pairs an outer policy or instrument with the other incident's evidence
- **THEN** validation refuses rather than mislabeling the isolation

#### Scenario: Legacy compatibility and default refusal
- **WHEN** existing legacy evidence is read, or quarantine authorization is absent
- **THEN** legacy isolation remains unchanged and unapproved quarantined serving remains refused
