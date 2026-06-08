# v2-operator-ui-console Specification (delta)

## ADDED Requirements

### Requirement: Operator UI SHALL NOT sanction training on a non-production inspection bundle

The operator UI SHALL NOT invite or accept using a one-off Tushare inspection
bundle (a `provider_uri` under `output/operator_ui/results/<job>/qlib_provider`)
as a training or backtest data source, because such a bundle is non-production
(no survivorship masking, ad-hoc adjust mode, no pipeline provenance) and
training on it silently diverges from the production bundle built by the
data-pipeline scripts. No UI copy SHALL direct the operator to paste an
inspection bundle's path into a training `provider_uri`; any UI surface that
references such a bundle SHALL carry an explicit do-not-train warning. The
training-input guard SHALL fail loud and refuse a `provider_uri` that points at
an `operator_ui/results/<job>/qlib_provider` inspection bundle, while a
production bundle (not under `operator_ui/results`) SHALL pass unaffected.

#### Scenario: a UI inspection bundle is rejected as a training source
- **WHEN** a training run's `provider_uri` points at an
  `…/operator_ui/results/<job>/qlib_provider` inspection bundle
- **THEN** the training-input guard fails loud with an explicit error and the
  run is refused
- **AND** the error directs the operator to use a production bundle built by the
  data-pipeline scripts

#### Scenario: a production bundle is accepted
- **WHEN** a training run's `provider_uri` points at a production bundle that is
  NOT under `operator_ui/results`
- **THEN** the non-production guard does not fire and the run proceeds through
  the remaining date / instrument / embargo checks

#### Scenario: UI copy does not invite training on an inspection bundle
- **WHEN** an operator-UI page references a Tushare inspection bundle's
  `qlib_provider` path
- **THEN** the copy carries an explicit do-not-train warning and never tells the
  operator to use it as a training `provider_uri`
