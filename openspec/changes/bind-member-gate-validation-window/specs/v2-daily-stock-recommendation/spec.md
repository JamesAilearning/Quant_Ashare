## ADDED Requirements

### Requirement: Member IC gates SHALL bind measured dates to the producer training configuration

Before measuring a member's IC gate, the runner SHALL bind the actual pickle
bytes to the parsed trainer sidecar's `pkl_sha256`, and SHALL bind the exact
producer-layout config bytes to that sidecar's `run_config_sha256`. The config
hash and parsed fields SHALL use a single byte read. The declared fit dates
SHALL equal the config's `train_start`/`train_end`; measured valid dates SHALL
equal its `valid_start`/`valid_end`. These config dates SHALL be real canonical
YYYY-MM-DD strings. Missing evidence SHALL NOT be filled from CLI defaults.

Unbindable evidence SHALL prevent dataset construction and member
deserialization, and SHALL produce an auditable FAIL artifact with the existing
IC block shape: `verdict: FAIL`, `ic_1d: null` and a reason stating the measurement
was not performed. The trainer-integrity result and normal FAIL exit code SHALL
remain available. An unreadable pickle SHALL retain the producer/tool error
boundary. The existing two member gates and gate schema version SHALL remain.

Rotation SHALL independently bind the incoming member gate's fit and valid dates
to the digest-bound producer config before loading models, backing up or
installing the candidate. It SHALL verify the sidecar bytes against the staged
manifest and retain strict serving-loader validation of actual pickle bytes.
This check SHALL apply to older PASS artifacts without an exception. All
existing span/gap/recency, plan, certification and bootstrap rules SHALL remain.

This requirement SHALL NOT introduce an independent unseen-test-period return
gate or change IC thresholds, canonical metrics or ensemble dry-run overlap.

#### Scenario: Another plausible validation window cannot pass the member gate
- **WHEN** either declared valid boundary differs from the bound training config,
  including a same-duration shifted window that satisfies existing broad bounds
- **THEN** the runner writes FAIL with unmeasured/null IC and does not score or
  deserialize the member

#### Scenario: Missing or altered evidence remains an auditable failure
- **WHEN** a readable member pickle has a missing/unusable sidecar or config,
  a mismatched digest, or a missing/malformed bound train or valid date
- **THEN** a FAIL artifact records the explicit evidence refusal and no IC value

#### Scenario: Older mismatched PASS artifact cannot authorize installation
- **WHEN** a correctly hashed old PASS gate carries a plausible window that is
  not the incoming run's exact valid window
- **THEN** rotation refuses before model loading, backup or installation and
  leaves the incumbent manifest unchanged without swap residue

#### Scenario: Bound matching evidence retains existing behavior
- **WHEN** the actual producer run and gate dates match and all existing checks pass
- **THEN** member scoring receives those exact fit/valid dates and retains its
  IC rule and artifact schema, and maintenance rotation can install normally
