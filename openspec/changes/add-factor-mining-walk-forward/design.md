# Design: Factor Mining Walk-Forward Integration (Phase 6.3 follow-up)

## Why this is a follow-up rather than part of Phase 6

The Phase 6 PR's tasks.md explicitly listed Phase 6.3 as operator
follow-up. The justification at the time — "real bake-off requires
the PIT bundle, which is not on disk" — was correct for the
**bake-off run**. It was **not** correct for the **wiring**:
`scripts/run_walk_forward.py` should accept
`feature_handler: "MinedFactor"` in its YAML and bind the registry
before the engine runs, regardless of whether a real PIT bundle is
present today. This change closes that wiring gap and ships the
comparison CLI; the bake-off run remains operator-gated.

## Module surface

```
scripts/
├── run_walk_forward.py                 # MODIFIED — accept mined-factor keys; bind
└── compare_factor_handlers.py          # NEW — JSON diff CLI

config_walk_mined.yaml                  # NEW — operator example

tests/logic/
├── test_run_walk_forward_mined.py      # NEW — YAML parsing + bind behaviour
└── test_compare_factor_handlers.py     # NEW — diff arithmetic + CLI smoke
```

No edits to `WalkForwardConfig`, `WalkForwardEngine`,
`FeatureDatasetBuilder`, or any file under `src/factor_mining/`.

## YAML schema extension (`scripts/run_walk_forward.py`)

Old allow-list (pre-PR):

```python
qlib_keys = {"provider_uri", "region"}
valid_fields = {f.name for f in WalkForwardConfig.__dataclass_fields__.values()}
unknown = sorted(set(raw) - valid_fields - qlib_keys)
if unknown:
    raise ValueError(f"Unknown config keys ...")
```

New allow-list:

```python
qlib_keys = {"provider_uri", "region"}
mined_factor_keys = {
    "mined_factor_pool_dir",
    "mined_factor_delisted_registry_path",
    "mined_factor_pit_provider_uri",
    "mined_factor_universe_name_override",
}
valid_fields = {f.name for f in WalkForwardConfig.__dataclass_fields__.values()}
unknown = sorted(set(raw) - valid_fields - qlib_keys - mined_factor_keys)
```

The `mined_factor_*` keys are **always allowed** in the YAML — even
when `feature_handler == "Alpha158"` — so an operator can keep them
prefilled in a base template and only flip `feature_handler` between
runs. They are **required** only when `feature_handler == "MinedFactor"`;
the required check happens after `WalkForwardConfig` construction so
the error message can reference the resolved handler name.

### Required-when-MinedFactor contract

```python
if wf_config.feature_handler == "MinedFactor":
    if not raw.get("mined_factor_pool_dir"):
        raise ValueError(
            "feature_handler='MinedFactor' requires mined_factor_pool_dir "
            "to be set. See docs/factor_mining/user_guide.md for the bind "
            "workflow."
        )
    if not raw.get("mined_factor_delisted_registry_path"):
        raise ValueError(
            "feature_handler='MinedFactor' requires "
            "mined_factor_delisted_registry_path to be set."
        )
```

### `mined_factor_pit_provider_uri` default

When not explicitly set, defaults to the top-level `provider_uri`.
This is the safe default — most operators want one PIT bundle driving
both qlib runtime and the MinedFactor handler's panel re-evaluation.
A divergence triggers a `_logger.warning(...)` (not an error — the
two-bundle setup is legitimate when comparing across PIT vintages).

### `RunWalkForwardConfig` dataclass

```python
@dataclass(frozen=True)
class RunWalkForwardConfig:
    wf: WalkForwardConfig
    qlib: QlibRuntimeConfig
    mined_factor_bundle: MinedFactorBundle | None = None
```

`_load_config` returns this; `main()` consumes it.

## Registration timing in `main()`

```python
def main() -> None:
    setup_logging()
    config_file = sys.argv[1] if len(sys.argv) > 1 else "config_walk.yaml"
    run_cfg = _load_config(config_file)

    init_qlib_canonical(run_cfg.qlib)
    if run_cfg.mined_factor_bundle is not None:
        register_mined_factor_handler(run_cfg.mined_factor_bundle, replace=True)

    result = WalkForwardEngine.run(run_cfg.wf)
    ...
```

`replace=True` is important: a single Python process running the
walk-forward twice (e.g. CI re-running an updated YAML) must re-bind
the registry slot to the new bundle without raising
"already registered". This matches Phase 5's
`v2-feature-handler-registry` MODIFIED requirement.

Registration MUST happen **after** `init_qlib_canonical` because
`MinedFactorBundle.__post_init__` validates the pool directory but
does not touch qlib — qlib is needed lazily inside the factory body
when the engine calls it.

## `config_walk_mined.yaml`

```yaml
# Walk-forward backtest with mined-factor handler.
# Operator workflow (per docs/factor_mining/user_guide.md):
#   1. Build the PIT bundle (inventory.md §F.3).
#   2. Run the miner: python -m src.factor_mining.miner config/factor_mining/default.yaml
#   3. Promote a run:  python -m src.factor_mining.promote --run <run_dir> --to v1
#   4. Fill in the pool_dir / delisted_registry_path below.
#   5. Run this config: python scripts/run_walk_forward.py config_walk_mined.yaml
#   6. Compare:        python scripts/compare_factor_handlers.py \
#                         output/walk_forward/walk_forward_report.json \
#                         output/walk_forward_mined/walk_forward_report.json

extends: config_walk.yaml

feature_handler: "MinedFactor"
output_dir: "output/walk_forward_mined"

# Operator-fill — pool directory and PIT registry path.
mined_factor_pool_dir: "research/mined_factors/production/v1"
mined_factor_delisted_registry_path: ""   # OPERATOR-FILL
# mined_factor_pit_provider_uri defaults to provider_uri (above); set
# only if you want the MinedFactor handler to re-evaluate against a
# different PIT vintage than the qlib runtime uses for training data.
```

## `scripts/compare_factor_handlers.py`

### CLI

```
python scripts/compare_factor_handlers.py BASELINE CANDIDATE [--out PATH] [--metrics LIST]
```

- `BASELINE` and `CANDIDATE`: paths to `walk_forward_report.json`
  files (output of two walk-forward runs).
- `--out PATH`: optional. If set, write a JSON manifest. If unset,
  print to stdout.
- `--metrics LIST`: optional comma-separated metric names. Defaults
  to the design doc's success-criterion set:
  `mean_information_ratio,mean_ic_1d,mean_annualized_return,worst_drawdown`.
- Exits 0 on a clean comparison; non-zero if either report is
  malformed.

### Diff arithmetic

For each metric `m`:

```
baseline_value = baseline_report["aggregate_metrics"][m]
candidate_value = candidate_report["aggregate_metrics"][m]
abs_delta = candidate_value - baseline_value
rel_delta = (candidate_value - baseline_value) / baseline_value if baseline_value != 0 else None
```

The output JSON shape:

```json
{
  "baseline_report": "<path>",
  "candidate_report": "<path>",
  "baseline_label": "Alpha158",
  "candidate_label": "MinedFactor",
  "metrics": {
    "mean_information_ratio": {
      "baseline": 0.31,
      "candidate": 0.45,
      "abs_delta": 0.14,
      "rel_delta": 0.4516
    },
    ...
  },
  "summary": {
    "candidate_better_count": 3,
    "baseline_better_count": 1,
    "design_doc_ir_threshold_met": true
  }
}
```

### `design_doc_ir_threshold_met`

Per design doc §10 success criterion: "Adding mined factors improves
OOS Sharpe ≥ 10% vs Alpha158-only baseline". The compare CLI codes
this as `candidate.mean_information_ratio >= 1.10 *
baseline.mean_information_ratio`. The summary surfaces a boolean so
the operator can grep for `"design_doc_ir_threshold_met": true` in
the PR-body bake-off paste.

### Baseline / candidate label inference

If the config block of either report has `feature_handler ==
"MinedFactor"`, that report is labelled "MinedFactor"; otherwise
"Alpha158" (or the literal handler name if it's neither). The CLI
accepts `--baseline-label` / `--candidate-label` overrides for
non-standard handler names.

## Test plan

### `tests/logic/test_run_walk_forward_mined.py`

Synthetic-config tests (no real walk-forward run):

- `test_load_config_with_mined_factor_keys_parses` — YAML with all
  four mined-factor keys parses without error.
- `test_load_config_alpha158_with_mined_factor_keys_warns_but_passes` —
  Alpha158 handler + mined-factor keys present is allowed (operator
  template scenario).
- `test_load_config_minedfactor_without_pool_dir_raises` — clear
  error referencing user_guide.md.
- `test_load_config_minedfactor_without_registry_raises`.
- `test_load_config_minedfactor_pit_uri_defaults_to_provider_uri`.
- `test_main_calls_register_mined_factor_handler` — mock
  `register_mined_factor_handler` and `WalkForwardEngine.run`;
  verify the bind happens with the expected bundle after qlib init
  and before engine run.

### `tests/logic/test_compare_factor_handlers.py`

Synthetic-report tests (write fake JSONs to tmp_path):

- `test_compare_writes_diff_json` — basic two-report diff.
- `test_compare_rel_delta_handles_zero_baseline` — divide-by-zero →
  `rel_delta: null`.
- `test_compare_design_doc_ir_threshold_met_true` — candidate IR is
  ≥ 1.1× baseline → flag true.
- `test_compare_design_doc_ir_threshold_met_false` — candidate IR
  below 1.1× → flag false.
- `test_compare_label_inference_from_config_handler` — labels
  inferred from the report's config.feature_handler.
- `test_compare_label_override` — `--baseline-label` /
  `--candidate-label` honoured.
- `test_compare_cli_subprocess` — full CLI smoke.

## Spec deltas

### `v2-feature-handler-registry` MODIFIED

Extend the existing "MinedFactor is registered only via explicit
bind" requirement: the authorised bind sites are now (a) the
application's pipeline-startup code per the user guide, AND (b)
`scripts/run_walk_forward.py` when its YAML carries
`feature_handler: "MinedFactor"` + the required
`mined_factor_pool_dir` / `mined_factor_delisted_registry_path`
keys.

### `v2-factor-mining-walk-forward` NEW (4 requirements)

1. **walk-forward CLI SHALL accept the mined-factor top-level YAML
   keys** — extends the allow-list.
2. **walk-forward CLI SHALL require pool_dir + registry when
   feature_handler == "MinedFactor"** — error pointing at user guide.
3. **walk-forward CLI SHALL bind via `register_mined_factor_handler`
   between qlib init and engine run** — `replace=True` semantics
   verified.
4. **compare_factor_handlers CLI SHALL emit IR threshold flag** —
   `design_doc_ir_threshold_met` exposed in JSON output per design
   doc §10.

## Risks & mitigations

| Risk | Mitigation |
|------|------------|
| `mined_factor_pit_provider_uri` silently diverges from `provider_uri` causing dataset / handler mismatch | When divergent, log a WARNING with both paths so the operator sees it in the run log; the divergence is sometimes intentional (cross-vintage compare) so we don't raise |
| Compare CLI on partial reports (one missing a metric) | Per-metric diff entry sets the missing side to null and excludes it from `candidate_better_count`; the JSON `unavailable_metrics` field lists the missing names |
| `register_mined_factor_handler` raises "already registered" on re-runs in the same process | The bind call uses `replace=True` |
| Walk-forward engine doesn't know about MinedFactor and tries to call its compute path with no panel | Phase 5's lazy qlib import means the handler factory is what wires `StaticDataLoader`; the engine just gets a qlib handler back. No engine changes needed |
| Operator runs the bake-off with `feature_handler: MinedFactor` but `mined_factor_pool_dir` points at an empty directory | `MinedFactorBundle.__post_init__` raises before the engine starts; the error message is preserved as Phase 5 spec'd |
| Test mocking `WalkForwardEngine.run` masks real wiring drift | A separate inspect-based test verifies that `main()` calls `register_mined_factor_handler` between `init_qlib_canonical` and `WalkForwardEngine.run` by reading the source AST, not just the mock call order |
