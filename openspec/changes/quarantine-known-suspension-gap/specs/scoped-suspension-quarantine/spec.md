## ADDED Requirements

### Requirement: Known suspension incident SHALL remain explicit and bounded
The system SHALL accept only the explicitly selected policy
`suspend-688766-20251127-20251209` for its eight exact historical business keys.
It MUST preserve prior raw evidence, publish only vendor-returned rows and keep
structured holes and incomplete status. It MUST NOT infer a safe omission from
the number of affected securities. Every later refresh SHALL compare with the
original complete incident reference until all keys are returned.

#### Scenario: Approved loss with unchanged other history
- **WHEN** the selected policy matches the only missing keys and evidence verifies
- **THEN** the exact candidate is published with a structured quarantine hole,
  immutable retained/reference evidence and exit 3, not reported as complete

#### Scenario: Different loss or invalid evidence
- **WHEN** another date, ticker, endpoint or conflicting payload is missing, or
  evidence cannot be verified
- **THEN** the exception SHALL refuse without replacing retained suspension data

#### Scenario: Repeated omission and recovery
- **WHEN** a later vendor refresh still omits an approved key
- **THEN** quarantine persists even when the latest retained file also lacks it
- **WHEN** every approved key returns and all other checks pass
- **THEN** a refreshed/rebuilt bundle clears quarantine but retains audit evidence

### Requirement: Qualified serving SHALL isolate rather than rewrite the universe
Daily recommendations SHALL require a separate matching policy opt-in for a
quarantined bundle. All other holes and missing prerequisites MUST still refuse.
The isolated security SHALL be excluded before Top-K with its unchanged score
and explicit audit reason retained; JSON, CSV and CLI SHALL disclose isolation
and incomplete data. Operator pages SHALL disclose current and historical
baseline isolation independently and MUST NOT advertise broad hole permission
as authorization for the incident. Existing holdings and historical universes MUST NOT change.

#### Scenario: Quarantined name ranks first
- **WHEN** SH688766 has the highest score under the active quarantine
- **THEN** it is absent from picks, retained as data_quarantine in the full audit,
  other scores/order are unchanged and eligible replacements fill Top-K

#### Scenario: Opt-in absent or other holes present
- **WHEN** quarantine opt-in is absent or additional gaps are recorded
- **THEN** ordinary production recommendation refuses, even if the known incident
  is present; the incident is never a blanket partial-data permission

#### Scenario: Historical metric run
- **WHEN** Pipeline, WalkForward, the shared BacktestRunner, frozen-model OOS
  evaluation or retrain qualification is invoked against a quarantined bundle
- **THEN** it refuses before output/runtime work, without removing the security
  from historical instruments, reporting a newly certified result or issuing
  ordinary rotation-qualification evidence
