# v2-feature-handler-registry Specification

## Purpose
TBD - created by archiving change add-feature-handler-registry. Update Purpose after archive.
## Requirements
### Requirement: Feature dataset builder SHALL use a handler registry

The feature dataset builder SHALL resolve `feature_handler` through a registry
of explicitly registered handler factories instead of hard-coded if/else
construction.

#### Scenario: default Alpha158 handler is requested
- **WHEN** `FeatureDatasetConfig.feature_handler` is `Alpha158`
- **THEN** the builder constructs the registered Alpha158 qlib handler
- **AND** existing Alpha158 configs remain compatible

#### Scenario: custom handler is registered
- **WHEN** a caller registers a custom handler name and factory
- **THEN** `FeatureDatasetBuilder` can build a dataset using that registered handler name

#### Scenario: unknown handler is requested
- **WHEN** `FeatureDatasetConfig.feature_handler` is not registered
- **THEN** validation raises `FeatureDatasetError`
- **AND** the error message lists registered handler names

### Requirement: Feature handler registration SHALL remain explicit

The system SHALL NOT import arbitrary handler classes from user config strings.
Only factories registered through the registry boundary SHALL be accepted.

#### Scenario: dotted import path is supplied as handler name
- **WHEN** a caller supplies an unregistered dotted import path as `feature_handler`
- **THEN** validation raises `FeatureDatasetError`
- **AND** no dynamic import is attempted

### Requirement: MinedFactor handler SHALL be registered via an explicit bind step

The `MinedFactor` family of feature handlers SHALL be registered only via an explicit `register_mined_factor_handler(bundle, *, name="MinedFactor")` call. Importing `src/data/mined_factor_handler.py` SHALL NOT register a default MinedFactor handler at module-import time, and SHALL NOT pull qlib into `sys.modules`. The authorised bind sites for the explicit call are: (a) the application's pipeline-startup code, per `docs/factor_mining/user_guide.md`; and (b) `scripts/run_walk_forward.py` when its YAML carries `feature_handler: "MinedFactor"` together with the required `mined_factor_pool_dir` and `mined_factor_delisted_registry_path` top-level keys (per `v2-factor-mining-walk-forward`). This preserves the existing pattern where `Alpha158` is seeded at import time but any custom handler is application-driven.

#### Scenario: a caller imports src.data.mined_factor_handler
- **WHEN** an application imports `src.data.mined_factor_handler`
- **THEN** `MinedFactor` is NOT in `list_supported_feature_handlers()`
- **AND** `qlib` is NOT present in `sys.modules`

#### Scenario: a caller registers a pool via the explicit bind helper
- **WHEN** an application calls `register_mined_factor_handler(MinedFactorBundle(pool_dir=...))`
- **THEN** `"MinedFactor"` appears in `list_supported_feature_handlers()`
- **AND** `FeatureDatasetConfig(feature_handler="MinedFactor", ...)` resolves to the bound factory

#### Scenario: the walk-forward CLI binds when YAML selects MinedFactor
- **WHEN** `scripts/run_walk_forward.py` is invoked with a YAML whose `feature_handler == "MinedFactor"` and which sets `mined_factor_pool_dir` and `mined_factor_delisted_registry_path`
- **THEN** after `init_qlib_canonical(...)` and before `WalkForwardEngine.run(...)`, `register_mined_factor_handler` is called with the `MinedFactorBundle` built from those YAML keys
- **AND** the bind uses `replace=True` so re-runs in the same Python process do not raise "already registered"

