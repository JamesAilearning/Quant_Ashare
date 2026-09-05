## ADDED Requirements

### Requirement: Baseline scan accounting SHALL count artifact reads, not gap verdicts

Baseline search `scanned` SHALL equal the number of reader callback invocations.
Detecting a history gap before reading the next artifact SHALL NOT increase this
count. The gap SHALL still produce an unknowable verdict and SHALL NOT permit
an older baseline to be accepted. Tests SHALL verify both initial and interior
gaps with synthetic artifacts, independently of local production output.

#### Scenario: an interior history gap stops after a verified HOLD
- **WHEN** lookup reads a HOLD artifact and encounters missing weekdays before
  the next indexed artifact
- **THEN** it reports a history gap with one scanned artifact and never reads
  the older artifact

#### Scenario: a gap precedes the first artifact
- **WHEN** missing weekdays separate the selected date from the newest artifact
- **THEN** it reports a history gap with zero scanned artifacts and never calls
  the artifact reader
