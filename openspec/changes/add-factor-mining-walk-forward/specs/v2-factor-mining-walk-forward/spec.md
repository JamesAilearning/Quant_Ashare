## ADDED Requirements

### Requirement: Walk-forward CLI SHALL accept mined-factor top-level YAML keys

`scripts/run_walk_forward.py`'s YAML loader SHALL accept four optional top-level keys in addition to the existing `provider_uri` / `region` / `WalkForwardConfig` field set: `mined_factor_pool_dir`, `mined_factor_delisted_registry_path`, `mined_factor_pit_provider_uri`, and `mined_factor_universe_name_override`. The strict "unknown YAML key → hard error" rule SHALL be preserved for any key not in this expanded allow-list. The mined-factor keys SHALL be accepted regardless of the `feature_handler` value, so operators can prefill a base template and toggle between handlers by changing only `feature_handler`.

#### Scenario: YAML with mined-factor keys and Alpha158 handler is accepted
- **WHEN** the walk-forward YAML has `feature_handler: "Alpha158"` and `mined_factor_pool_dir: "research/mined_factors/production/v1"`
- **THEN** `_load_config` parses without error
- **AND** the returned `RunWalkForwardConfig.mined_factor_bundle` is None (the bundle is built only when MinedFactor is selected)

#### Scenario: unknown YAML key is still rejected
- **WHEN** the walk-forward YAML contains a top-level key that is neither in `WalkForwardConfig.__dataclass_fields__`, the qlib runtime keys (`provider_uri`, `region`), nor the four mined-factor keys
- **THEN** `_load_config` raises with a clear error listing the unknown keys

### Requirement: Walk-forward CLI SHALL require pool and registry paths when feature_handler is MinedFactor

When `feature_handler == "MinedFactor"`, `scripts/run_walk_forward.py` SHALL require both `mined_factor_pool_dir` and `mined_factor_delisted_registry_path` to be non-empty strings. Either missing SHALL produce a hard error message referencing `docs/factor_mining/user_guide.md` before qlib initialisation. `mined_factor_pit_provider_uri` SHALL default to the top-level `provider_uri` when not explicitly set; an explicit value that differs from `provider_uri` SHALL log a WARNING (cross-vintage compare is legitimate but worth surfacing).

#### Scenario: missing pool_dir under MinedFactor handler
- **WHEN** the YAML has `feature_handler: "MinedFactor"` but omits `mined_factor_pool_dir`
- **THEN** `_load_config` raises with an error message naming `mined_factor_pool_dir` and pointing at `docs/factor_mining/user_guide.md`
- **AND** no call is made to `init_qlib_canonical` or any factor-mining module

#### Scenario: pit_provider_uri defaults to provider_uri
- **WHEN** the YAML has `feature_handler: "MinedFactor"`, `provider_uri: "/data/pit"`, and no `mined_factor_pit_provider_uri`
- **THEN** the returned `MinedFactorBundle.pit_provider_uri` equals `/data/pit`

#### Scenario: explicit pit_provider_uri divergence is warned
- **WHEN** the YAML sets `provider_uri` and `mined_factor_pit_provider_uri` to different paths
- **THEN** `_load_config` returns a valid `RunWalkForwardConfig` (does NOT raise)
- **AND** a WARNING is logged naming both paths

### Requirement: Walk-forward CLI SHALL bind MinedFactor between qlib init and engine run

`scripts/run_walk_forward.py::main` SHALL invoke `register_mined_factor_handler(bundle, replace=True)` strictly after `init_qlib_canonical(run_cfg.qlib)` and strictly before `WalkForwardEngine.run(run_cfg.wf)`, but only when `run_cfg.mined_factor_bundle is not None`. The `replace=True` SHALL be used so re-runs of the same script in a single Python process re-bind the registry slot without raising. The ordering guarantee makes the bind observable to the engine's first fold.

#### Scenario: ordering is preserved
- **WHEN** a maintainer inspects `scripts/run_walk_forward.py::main` source
- **THEN** the lexical order is `init_qlib_canonical(...)` → conditional `register_mined_factor_handler(...)` → `WalkForwardEngine.run(...)`
- **AND** the bind is wrapped in an `if mined_factor_bundle is not None:` check so Alpha158 runs are unaffected

#### Scenario: re-running in the same process succeeds
- **WHEN** `main` is invoked twice in the same Python process with the same MinedFactor YAML
- **THEN** the second invocation does NOT raise "already registered"
- **AND** the registry slot is re-bound to the second invocation's bundle

### Requirement: compare_factor_handlers CLI SHALL emit a design-doc IR threshold flag

`scripts/compare_factor_handlers.py` SHALL accept two paths to `walk_forward_report.json` files (baseline + candidate) and SHALL produce a JSON manifest comparing the aggregate metrics. The manifest SHALL include, per-metric, `baseline`, `candidate`, `abs_delta`, and `rel_delta` (null when `baseline == 0`). The default metric set SHALL be `mean_information_ratio`, `mean_ic_1d`, `mean_annualized_return`, `worst_drawdown` (the design doc §10 success-criterion set). The manifest SHALL also include a `summary.design_doc_ir_threshold_met` boolean that is `True` iff `candidate.mean_information_ratio >= 1.10 * baseline.mean_information_ratio` (design doc §10: "OOS Sharpe ≥ 10% vs Alpha158-only baseline").

#### Scenario: candidate IR is 12% above baseline
- **WHEN** the candidate report has `mean_information_ratio = 0.45` and the baseline has `mean_information_ratio = 0.40`
- **THEN** `summary.design_doc_ir_threshold_met` is `true`
- **AND** the per-metric entry has `abs_delta = 0.05` and `rel_delta ≈ 0.125`

#### Scenario: candidate IR is only 5% above baseline
- **WHEN** the candidate IR is `0.42` and baseline IR is `0.40`
- **THEN** `summary.design_doc_ir_threshold_met` is `false`

#### Scenario: baseline IR is zero
- **WHEN** baseline IR is `0` and candidate IR is `0.30`
- **THEN** `rel_delta` for `mean_information_ratio` is `null` (divide-by-zero guard)
- **AND** `summary.design_doc_ir_threshold_met` is `true` (because `0.30 >= 1.10 * 0 == 0`)

#### Scenario: candidate report missing a metric
- **WHEN** the candidate report's `aggregate_metrics` lacks one of the default metrics
- **THEN** that metric is omitted from `metrics` and appears in `unavailable_metrics`
- **AND** the `summary` counts do not include the unavailable metric
