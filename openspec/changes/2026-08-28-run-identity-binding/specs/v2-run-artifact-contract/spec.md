# Delta for v2-run-artifact-contract

## ADDED Requirements

### Requirement: Every non-dry-run SHALL record its identity in its own artifacts

A run SHALL write its identity into the artifacts it produces: a run id and the
config fingerprint. Recording identity only in a separate catalog is not enough
— the directory on disk cannot then answer which run its bytes belong to, and a
reader holding only the directory has nothing to check.

The source revision and timestamps are ALREADY written and SHALL NOT be
reimplemented; the gap this closes is identity, not lineage.

The fingerprint SHALL be computed by the implementation the resume logic already
uses, which deliberately excludes the output directory so that renaming it does
not change a config's identity. A second hash implementation is FORBIDDEN: two
fingerprints of "the same config" that disagree are worse than one, because each
reader picks whichever it happens to import.

The artifact directory SHALL additionally carry a marker naming the run that
currently owns it, so ownership survives independently of any index file.

Artifacts written before this requirement carry none of these fields. Readers
SHALL treat their absence as LEGACY, never as corruption.

#### Scenario: a completed run names itself in its report

- **WHEN** a non-dry-run walk-forward run completes
- **THEN** its report carries the run id and the config fingerprint, alongside
  the source revision it already recorded
- **AND** the output directory carries a marker naming that run

#### Scenario: an artifact predating the requirement is legacy, not corrupt

- **GIVEN** a report written without identity fields
- **WHEN** a reader inspects it
- **THEN** it is reported as legacy rather than as a corrupt artifact

### Requirement: A directory SHALL NOT be silently overwritten by a different config

A run SHALL refuse to start when its output directory is already owned by a run
whose config fingerprint differs from its own, and SHALL name both fingerprints
and the directory — unless the operator explicitly opts in to overwriting.

The engine already computes the fingerprint and already detects this exact
mismatch; today it logs a warning and overwrites. The warning is in the right
place — what follows it is wrong. Measured on this repository, 72 of 105
catalogued runs point at artifacts a later run has overwritten, and one directory
holds 59 rows spanning 3 distinct fingerprints, so the overwriting is not
idempotent re-running.

Reuse under an IDENTICAL fingerprint SHALL remain permitted: resume discovers
prior folds from that same directory, so minting a fresh directory per run would
disable resume entirely. Identity is bound to the directory's contents, not to
its path.

#### Scenario: a different config refuses to overwrite

- **GIVEN** an output directory owned by a run with one config fingerprint
- **WHEN** a run with a different fingerprint targets that directory without an
  explicit overwrite opt-in
- **THEN** it refuses to start and names both fingerprints and the directory

#### Scenario: the same config still resumes in place

- **GIVEN** an output directory owned by a run with a given config fingerprint
- **WHEN** a run with the SAME fingerprint targets it
- **THEN** it proceeds and resume discovers the prior folds

### Requirement: An output directory SHALL admit one run at a time

A run SHALL hold exclusive claim on its output directory for its duration, and a
second run targeting the same directory SHALL refuse to start rather than write
into it concurrently.

Without this, two processes interleave fold files into one directory and the
aggregate report is whichever finished last — a state that leaves no ordering in
the catalog to reveal it happened, and that is indistinguishable to a reader from
a benign sequential re-run. The existing catalog lock does not cover this: its
scope is the index file alone.

The operating runbook currently states the serialisation rule in prose only.
A rule that matters SHALL be enforced by the machine.

#### Scenario: a concurrent run on the same directory refuses

- **GIVEN** a run holding its output directory
- **WHEN** a second run targets the same directory
- **THEN** the second refuses to start and says which run holds it
