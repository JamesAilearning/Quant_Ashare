# Tasks: Factor Mining Walk-Forward (Phase 6.3 follow-up)

## OpenSpec (propose stage)

- [x] Draft proposal.md / design.md / tasks.md
- [x] Draft `specs/v2-feature-handler-registry/spec.md` deltas
      (MODIFIED — authorised bind sites)
- [x] Draft `specs/v2-factor-mining-walk-forward/spec.md` (NEW
      capability, 4 requirements)
- [x] `openspec validate add-factor-mining-walk-forward --strict` —
      green

## Implementation

### Walk-forward CLI extension
- [x] `RunWalkForwardConfig` dataclass in `scripts/run_walk_forward.py`
      (wf + qlib + optional MinedFactorBundle)
- [x] `_load_config` accepts the four `mined_factor_*` top-level YAML
      keys; preserves the existing unknown-key strict rejection
- [x] When `feature_handler == "MinedFactor"`,
      `mined_factor_pool_dir` and
      `mined_factor_delisted_registry_path` are required (clear error
      pointing at user_guide.md)
- [x] `mined_factor_pit_provider_uri` defaults to top-level
      `provider_uri`; logs a WARNING if the two diverge
- [x] `main()` calls `register_mined_factor_handler(bundle,
      replace=True)` after `init_qlib_canonical(...)` and before
      `WalkForwardEngine.run(...)`

### Example config
- [x] `config_walk_mined.yaml` extends `config_walk.yaml`, sets
      `feature_handler: "MinedFactor"` and `output_dir:
      "output/walk_forward_mined"`, includes operator-fill
      placeholders for pool_dir and registry path

### Compare CLI
- [x] `scripts/compare_factor_handlers.py` — JSON diff CLI:
      - reads two `walk_forward_report.json` paths
      - diffs the four design-doc success-criterion metrics by default
      - `--metrics`, `--out`, `--baseline-label`, `--candidate-label`
        knobs
      - exits 0 on clean diff
      - emits `design_doc_ir_threshold_met` flag per design doc §10
        (candidate.mean_information_ratio ≥ 1.10 × baseline)

## Tests

### `tests/logic/test_run_walk_forward_mined.py`
- [x] `test_load_config_with_mined_factor_keys_parses`
- [x] `test_load_config_alpha158_with_mined_factor_keys_allowed`
- [x] `test_load_config_minedfactor_without_pool_dir_raises`
- [x] `test_load_config_minedfactor_without_registry_raises`
- [x] `test_load_config_pit_uri_defaults_to_provider_uri`
- [x] `test_load_config_unknown_key_still_rejected`
- [x] `test_main_binds_handler_between_qlib_init_and_engine_run`
      (subprocess-free; mock all four call sites)

### `tests/logic/test_compare_factor_handlers.py`
- [x] `test_compare_basic_diff`
- [x] `test_compare_zero_baseline_emits_null_rel_delta`
- [x] `test_compare_ir_threshold_met_true`
- [x] `test_compare_ir_threshold_met_false`
- [x] `test_compare_handler_labels_inferred_from_config`
- [x] `test_compare_label_overrides_from_cli`
- [x] `test_compare_missing_metric_listed_in_unavailable`
- [x] `test_compare_cli_subprocess_smoke`
- [x] `test_compare_cli_exits_nonzero_on_missing_report`

## Validation

- [x] `pytest tests/logic/ -q` — all green
- [x] `ruff check src/ tests/ scripts/` — green (full-repo, as CI
      does on ubuntu-latest 3.11)
- [x] `openspec validate add-factor-mining-walk-forward --strict` —
      green
- [x] D5 grep still zero matches under `src/factor_mining/`

## Operator follow-up (gated on PIT bundle)

After this PR is merged the bake-off remains an operator action:

- [ ] Build the PIT bundle per `inventory.md` §F.3.
- [ ] Run `python -m src.factor_mining.miner …` on real PIT data.
- [ ] `python -m src.factor_mining.promote --run … --to v1`.
- [ ] Edit `config_walk_mined.yaml` placeholders (pool_dir +
      registry path).
- [ ] Run baseline:
      `python scripts/run_walk_forward.py config_walk.yaml`
- [ ] Run mined:
      `python scripts/run_walk_forward.py config_walk_mined.yaml`
- [ ] Compare:
      `python scripts/compare_factor_handlers.py
       output/walk_forward/walk_forward_report.json
       output/walk_forward_mined/walk_forward_report.json
       --out output/walk_forward_compare/compare.json`
- [ ] Paste the JSON's `summary.design_doc_ir_threshold_met` +
      per-metric diffs into the operator follow-up issue / PR.

## Deferred (NOT this proposal)

- Multi-vintage PIT comparison (one bundle vs another).
- Walk-forward UI surface in the operator web app.
- Auto-promotion based on the bake-off output (D4 manual gate
  stands).
- GPU.
- Real-data run on this machine.
