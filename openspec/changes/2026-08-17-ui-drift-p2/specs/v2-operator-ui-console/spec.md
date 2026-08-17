# Delta for v2-operator-ui-console

## ADDED Requirements

### Requirement: The config page SHALL offer only presets it can actually run

The preset selector SHALL list only presets whose shape this page can
emit. Campaign-frozen configurations (pre-registration / certification
evidence) SHALL NOT appear as ordinary options: the page emits a
standalone config, does not resolve `extends`, and has no widgets for
the cadence / scope / output keys those files carry — selecting one
would display its name while emitting a daily pipeline config, i.e. the
cadence the operator reads is not the cadence that will run.

The runnable/frozen judgment SHALL be derived from a marker the two
runtimes reject and the UI always writes (`mode`), so it stays correct
without a hand-maintained registry. Frozen presets SHALL still be
visible read-only, with the reasons they cannot run and the command-line
way to reproduce them. Active-preset detection SHALL consider runnable
presets only, so the reported preset is always one the selector offers.

#### Scenario: campaign files are not selectable but are visible

- **WHEN** the config page renders
- **THEN** campaign-frozen presets are absent from the selector
- **AND** they are listed read-only with why they cannot run here

#### Scenario: a newly saved preset needs no registration

- **GIVEN** the operator saves a preset from this page
- **WHEN** the page reloads
- **THEN** it appears as a runnable option without any registry edit

### Requirement: The page SHALL NOT describe research presets as production

No preset SHALL be labelled as the production configuration. The page
SHALL state that it emits daily research configs, and SHALL name where
the production serving configuration actually lives. A preset whose
universe/benchmark/topk coincide with production SHALL still be
described by what it is, because coincidence on some fields is exactly
what makes an operator read it as a production copy.

#### Scenario: the full-market baseline is named honestly

- **WHEN** the operator reads the preset help
- **THEN** the all-market preset is described as a baseline, not as
  production
- **AND** the production serving configuration is named

### Requirement: Result surfaces SHALL declare their cost/benchmark convention

Every rendered performance number SHALL state its convention. Numbers
derived from the engine's return series are **absolute gross** (neither
benchmark nor cost removed — the upstream series adds realized cost back
into the already-cost-deducted return); numbers derived from the risk
analysis are **net excess**. Where both conventions appear on one card,
each SHALL carry its own label.

Where two surfaces render the same-sounding quantity under different
conventions — notably the max-drawdown card (net excess, arithmetic
accumulation) versus the drawdown chart (absolute gross, geometric
accumulation) — the page SHALL state that they are not the same number
and SHALL name EVERY axis of the difference (benchmark, cost,
accumulation method). Attributing the gap to cost alone is prohibited:
cost is typically the smallest of the three, so a cost-only explanation
leaves the discrepancy unexplained.

#### Scenario: gross numbers are not read as net

- **WHEN** the results page renders the primary metric, total return,
  NAV curve or monthly returns
- **THEN** each states it is absolute gross

#### Scenario: the two drawdowns are reconciled

- **WHEN** the risk card and the drawdown chart are both rendered
- **THEN** the page states they are not the same number
- **AND** names benchmark, cost AND accumulation method as the reasons
