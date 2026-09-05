## ADDED Requirements

### Requirement: Quarterly incoming members match registered training family

The rotation executor SHALL bind the incoming member's actual producer config to
the registered training family before loading models, writing a backup or
installing a manifest, even when both prior light-gate artifacts say PASS. This
is member eligibility under the same-family maintenance protocol, not a new
performance gate or strategy recertification.

The authority SHALL be `config.yaml` and the three committed bootstrap member
presets at the SAME pinned mainline revision used for certification. Each preset
SHALL retain the established base inheritance and declare instruments, benchmark,
the guard trio and device. Their non-window surfaces SHALL agree. Comparison
SHALL cover every declared field other than `extends` and the six train/valid/test
boundaries, plus bootstrap's existing same-family keys and explicit defaults.
Existing date, source, light-gate and serving checks SHALL remain unchanged.
Registered boolean values SHALL match boolean identity, never numeric aliases
such as `1` or `0`; conversely booleans SHALL NOT substitute for numeric fields.
Equal integer/float representations of numeric fields remain equivalent. This
scalar comparison rule SHALL be shared by bootstrap and rotation family checks.

Expected provider identity SHALL use only committed template defaults and the
canonical qlib path normalizer, never a live environment override. The exact
config bytes parsed for this comparison SHALL match the digest in the same
manifest-bound sidecar buffer used by execution's factual/source checks.

#### Scenario: A truthful retuned member cannot ride old PASS artifacts
- **WHEN** an incoming model has valid dates, clean registered source, a consistent
  digest chain and PASS gates but any registered family field differs or is absent
- **THEN** execution refuses before model loading, backup and installation
- **AND** the incumbent and gate artifacts remain byte-identical, staging is
  removed and the advisory lock is released without deleting its lockfile

#### Scenario: Rolling dates preserve the registered family
- **WHEN** the incoming member differs from bootstrap only in its six date fields
  and satisfies the existing factual, window and light-gate checks
- **THEN** family eligibility passes without imposing frozen bootstrap dates

#### Scenario: Local configuration cannot redefine registration
- **WHEN** the worktree config or live provider environment matches a retuned
  member but the pinned committed registration does not
- **THEN** execution rejects the member using the pinned registration
- **AND** a mainline ref moving mid-execution does not change that authority

#### Scenario: Boolean and numeric aliases do not change registered types
- **WHEN** a digest-bound config uses `1` or `1.0` for a registered true guard,
  or `false` for registered numeric zero
- **THEN** family comparison refuses before model loading and installation
- **AND** literal registered booleans and equal numeric forms such as `50` and
  `50.0` remain accepted

#### Scenario: Registration or reread evidence is unusable
- **WHEN** a registered file is missing, malformed, incomplete or inconsistent,
  its Git read fails or times out, a required family key has no explicit default,
  or the config read for family comparison no longer matches the bound sidecar
- **THEN** execution refuses through its classified refusal path without installation

#### Scenario: Equivalent provider spellings retain existing identity semantics
- **WHEN** the recorded provider path and committed default normalize to the same
  canonical qlib path and every other family requirement is satisfied
- **THEN** the member is not refused merely for that spelling difference
- **AND** this comparison makes no claim about immutable data snapshot identity
