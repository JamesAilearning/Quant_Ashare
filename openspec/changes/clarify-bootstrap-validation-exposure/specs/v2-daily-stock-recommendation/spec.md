## ADDED Requirements

### Requirement: Bootstrap dry-run documentation SHALL disclose validation exposure

The production runbook SHALL distinguish a bootstrap ensemble behavior dry run
from independent unseen-data performance validation. Its first-launch gate step
SHALL retain the registered window and disclose any overlap with the registered
member validation windows using the committed preset and gate evidence.
Being after the training windows SHALL NOT be presented as proof of independence
from validation, early stopping or model selection. The explanation SHALL retain
the existing campaign/annual-recertification performance authority and SHALL NOT
retroactively change gate results, registered dates or runtime semantics.

#### Scenario: Bootstrap v2 overlaps m3 validation

- **WHEN** the runbook describes the 2026-05-06..2026-07-31 ensemble dry run
- **THEN** it identifies m3 valid=2026-04-07..2026-07-07 and the
  2026-05-06..2026-07-07 overlap, explains early-stopping/model-selection exposure,
  and links the committed preset and corresponding gate evidence
- **AND** it does not label the complete dry-run window fully unseen for all
  three members or independent out-of-sample performance certification

#### Scenario: A shorter diagnostic interval is not promoted to certification

- **WHEN** the runbook mentions m3's registered 2026-07-10..2026-07-31 test window
- **THEN** it labels that interval an embedded daily diagnostic, not automatic
  independent ensemble performance certification
- **AND** passing the behavior gate or trimming dates after valid_end does not
  establish a new official metric or replace campaign/annual recertification
