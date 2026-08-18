# Delta for v2-operator-ui-console

## ADDED Requirements

### Requirement: The config page SHALL emit every precondition its universe demands

A configuration the page reports as valid SHALL be constructible by the
engine it is submitted to. Where a universe carries construction
preconditions — `instruments="csi800"` requires
`attribution_sleeve_grouping=True`, `risk_constraints_enabled=True` and
`risk_constraints_calibration="campaign_v1"`, because official csi800
metrics without the sleeve report and the campaign constraint are not
comparable to the certified numbers — the page SHALL carry those fields in
the emitted config, expose them as controls, and refuse loudly before
launch when they are unmet.

The refusal verdict SHALL be delegated to the canonical validator rather
than restated in the UI (a UI copy of the rule is the drift that produced
the defect), and SHALL run on BOTH the render path and the submit recheck
(the operator can flip a control and click Run inside the still-enabled
frame). Preset switching SHALL normalize these fields through the SAME
reset map the cost fields use, so neither family can carry a stale value
from a previously selected preset.

Defaults for these fields SHALL mirror the canonical dataclass defaults,
NOT the csi800 contract values: a default that silently stamps campaign
semantics onto a non-csi800 run changes what that run measures.

#### Scenario: csi800 without the guard triple is refused before launch

- **GIVEN** `instruments=csi800` with any of the three guards unset
- **WHEN** the page renders, or the operator submits
- **THEN** the page refuses with the canonical validator's message naming
  every unmet precondition, and no job is started

#### Scenario: a valid csi800 config is constructible

- **GIVEN** `instruments=csi800` with all three guards set to the contract
  values
- **WHEN** the config is submitted
- **THEN** the emitted config carries the triple and the engine constructs
  it without raising

#### Scenario: other universes are not forced into campaign semantics

- **GIVEN** `instruments` is not `csi800`
- **WHEN** the page renders
- **THEN** the guards stay at their canonical defaults and no refusal fires
