## ADDED Requirements

### Requirement: MinedFactor handler SHALL be registered via an explicit bind step

The `MinedFactor` family of feature handlers SHALL be registered only via an explicit `register_mined_factor_handler(bundle, *, name="MinedFactor")` call. Importing `src/data/mined_factor_handler.py` SHALL NOT register a default MinedFactor handler at module-import time, and SHALL NOT pull qlib into `sys.modules`. This preserves the existing pattern where `Alpha158` is seeded at import time but custom handlers (e.g. MinedFactor with a specific pool binding) require an explicit application-startup call.

#### Scenario: a caller imports src.data.mined_factor_handler
- **WHEN** an application imports `src.data.mined_factor_handler`
- **THEN** `MinedFactor` is NOT in `list_supported_feature_handlers()`
- **AND** `qlib` is NOT present in `sys.modules`

#### Scenario: a caller registers a pool via the explicit bind helper
- **WHEN** an application calls `register_mined_factor_handler(MinedFactorBundle(pool_dir=...))`
- **THEN** `"MinedFactor"` appears in `list_supported_feature_handlers()`
- **AND** `FeatureDatasetConfig(feature_handler="MinedFactor", ...)` resolves to the bound factory
