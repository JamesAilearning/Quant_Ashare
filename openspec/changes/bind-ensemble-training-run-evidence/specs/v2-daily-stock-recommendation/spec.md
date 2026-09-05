## ADDED Requirements

### Requirement: Ensemble members SHALL bind declared training windows to producer config bytes

Before deserializing each ensemble member, the serving loader SHALL require
the resolved training config at the pipeline producer layout
`<run>/config.yaml` relative to `<run>/artifacts/<model>.pkl`. An unknown
layout, an additional `artifacts/config.yaml`, or unreadable/non-mapping
training config SHALL refuse the ensemble with `EnsembleServingError`.
The config SHA-256 SHALL be calculated from the same bytes that are parsed
and SHALL match the valid lowercase 64-hex `run_config_sha256` in the
already manifest-hash-verified trainer sidecar. Missing or mismatched
evidence SHALL NOT receive a compatibility fallback.

The config's `train_start` and `train_end` SHALL be canonical YYYY-MM-DD
strings representing real dates and SHALL equal the member's `fit_start`
and `fit_end` respectively. Comparing duration alone or filling missing
config fields from the manifest SHALL NOT authorize a member. A failure
SHALL occur before that member's pickle is deserialized and SHALL propagate
through existing serving, ensemble gate, rotation, and bootstrap callers.
The existing loader interface, per-member load order, blend mathematics,
and canonical metric source SHALL remain unchanged.

Bootstrap and serving SHALL use the same producer-layout reader and config
digest validator. Bootstrap SHALL preserve its existing reader refusal
contract and `CutoverRefusal` error boundary. This requirement proves facts
about the persisted training run only; it SHALL NOT be represented as
registered-family, source-ancestry, or gate-valid-window certification.

#### Scenario: Plausible declared dates do not match the actual training run
- **WHEN** a manifest has valid quarterly window arithmetic but either fit
  boundary differs from the digest-bound config's corresponding train boundary
- **THEN** loading refuses before deserializing that member, including when
  the two windows have the same duration

#### Scenario: Post-training config edits break the member chain
- **WHEN** config bytes differ from the trainer sidecar's declared config digest
- **THEN** loading refuses even though the pickle and sidecar still match the manifest

#### Scenario: Missing or ambiguous config evidence cannot be filled from declarations
- **WHEN** the config is missing, unreadable, not a mapping, in an unknown
  layout, or shadowed by an artifacts-directory config
- **THEN** the member is refused without inferring its training dates

#### Scenario: Bound producer artifacts remain loadable
- **WHEN** all existing hash/version checks pass and the producer config bytes
  match the sidecar digest and declared train boundaries
- **THEN** the member loads with the existing interface and prediction behavior,
  without requiring invented fit or universe fields in the trainer sidecar

#### Scenario: Rotation cannot install an unbound member
- **WHEN** a staged rotation member fails config provenance or window binding
- **THEN** execution refuses through its existing member-chain check and leaves
  the incumbent manifest unchanged without creating a rotation backup
