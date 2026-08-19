## ADDED Requirements

### Requirement: The configuration page SHALL present research settings progressively

The configuration page SHALL identify itself as a research-only launcher and
SHALL organize existing controls into research goal/preset, data scope,
strategy constraints, advanced settings, and a final review.  Advanced
settings SHALL be collapsed by default.  The page SHALL allow the operator to
return to and edit any previously shown setting before launch.  It SHALL NOT
expose production-serving configuration or describe a research run as a
production promotion.

#### Scenario: standard research configuration

- **WHEN** an operator opens the configuration page and selects a runnable
  research preset
- **THEN** the research-only boundary and the progressive configuration
  sections are visible
- **AND** the operator can reach the final review without editing advanced
  controls

#### Scenario: advanced configuration change

- **WHEN** an operator expands advanced settings and changes a cost, model, or
  compute control
- **THEN** the changed value is visible in the final review
- **AND** unchanged fields retain their existing configuration semantics

### Requirement: The configuration page SHALL review the exact launch payload

Before the operator starts a research run, the configuration page SHALL show a
read-only, human-readable summary derived from the exact configuration mapping
that it will submit.  The review SHALL identify the selected mode and selected
preset, SHALL show all emitted settings, and SHALL make differences from a
loadable selected preset visible.  A missing or unreadable preset SHALL be
reported as unavailable for comparison; the page SHALL NOT fabricate a
baseline or silently omit the reason.

#### Scenario: preset configuration has no differences

- **WHEN** the emitted configuration matches the selected loadable preset
- **THEN** the review states that there are no preset differences
- **AND** it still displays the complete emitted configuration summary

#### Scenario: a setting differs from the preset

- **WHEN** an operator changes an emitted setting from the selected preset
- **THEN** the review identifies that setting and its emitted value
- **AND** the run action continues to submit the same mapping shown in the
  review

### Requirement: Configuration reorganization SHALL preserve launch semantics

For the same widget inputs, the configuration page SHALL preserve the existing
configuration key set, defaults, preset application order, validation results,
and CLI-compatible job launch behavior.  The reorganization SHALL NOT
introduce another configuration builder, silently change values, or alter
production-serving configuration.

#### Scenario: equivalent research launch

- **WHEN** an operator uses the same settings before and after the
  reorganization
- **THEN** validation receives the same configuration mapping and the selected
  mode is passed to the same job-launch path
- **AND** no production-serving configuration is written or modified
