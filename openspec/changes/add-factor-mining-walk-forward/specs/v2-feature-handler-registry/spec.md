## MODIFIED Requirements

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
