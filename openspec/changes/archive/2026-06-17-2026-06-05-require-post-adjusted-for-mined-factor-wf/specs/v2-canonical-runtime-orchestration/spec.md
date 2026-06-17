# Spec delta: v2-canonical-runtime-orchestration

## ADDED Requirements

### Requirement: PIT-handler walk-forward configs SHALL require post_adjusted

A `WalkForwardConfig` using a PIT feature handler SHALL require `adjust_mode ==
"post_adjusted"` (the PIT handler today is `"MinedFactor"`).

The PIT bin bundle is written post-adjusted and `PITDataProvider` initialises
the canonical qlib runtime in `post_adjusted` mode, so mined factor values are
physically constructed on post-adjusted prices. Evaluating them under a
different runtime adjustment mode would either abort every fold with a cryptic
`QlibRuntimeInitError` (the single-canonical-runtime guard rejecting the
mismatched second init) or — if that guard were relaxed — silently score the
factors against mismatched prices. The constraint SHALL therefore be enforced
at `WalkForwardConfig` construction (`__post_init__`), raising a typed
`WalkForwardError` whose message names the offending `adjust_mode` and the
required value, BEFORE any qlib init, feature building, model training, or
backtest. Non-PIT handlers (e.g. `"Alpha158"`) SHALL be unaffected and MAY use
any supported `adjust_mode`.

#### Scenario: MinedFactor handler with a non-post adjust_mode is rejected at construction
- **WHEN** a caller constructs `WalkForwardConfig(feature_handler="MinedFactor", adjust_mode="pre_adjusted")`
- **THEN** a typed `WalkForwardError` is raised at construction time
- **AND** the message states the PIT/MinedFactor path requires `adjust_mode: "post_adjusted"` and names the offending value
- **AND** no qlib runtime init, feature building, or backtest has run

#### Scenario: MinedFactor handler with post_adjusted is accepted
- **WHEN** a caller constructs `WalkForwardConfig(feature_handler="MinedFactor", adjust_mode="post_adjusted")`
- **THEN** construction succeeds with no error

#### Scenario: a non-PIT handler is unaffected by the PIT post-adjusted rule
- **WHEN** a caller constructs `WalkForwardConfig(feature_handler="Alpha158", adjust_mode="pre_adjusted")`
- **THEN** construction succeeds — the post-adjusted requirement applies only to PIT feature handlers
