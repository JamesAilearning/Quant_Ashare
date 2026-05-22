# Add Factor Mining Walk-Forward Integration — Phase 6.3 follow-up

## Why

The Phase 6 PR (`add-factor-mining-validation`, archived) shipped the
IS/OOS validator + promotion CLI + user guide, but explicitly deferred
the design doc's **Phase 6.3 walk-forward hook** to operator
follow-up. That deferral was justified at the time (no PIT bundle on
disk → no real bake-off possible), but it left a real **code gap**:
the existing `scripts/run_walk_forward.py` rejects `feature_handler:
"MinedFactor"` because the handler is never bound at app-startup
time, so the registry resolves the name to "unknown".

This change closes the gap. Concretely:

- The walk-forward CLI MAY now select `feature_handler: "MinedFactor"`
  in its YAML config, with two extra top-level keys
  (`mined_factor_pool_dir` and `mined_factor_delisted_registry_path`)
  pointing at the run / production directory the Phase 3 miner wrote.
- A new `scripts/compare_factor_handlers.py` CLI reads two
  walk-forward reports (one Alpha158 baseline, one MinedFactor) and
  produces a side-by-side JSON diff of the design doc's success
  criteria — `mean_ic_1d`, `mean_information_ratio`, `mean_annualized_return`,
  `worst_drawdown`.
- A `config_walk_mined.yaml` example extends `config_walk.yaml` so an
  operator copies it, fills in the pool / registry paths, and runs.

This delivers the design doc's Phase 6.3 row:

> **6.3 Walk-forward hook** | integration with `config_walk_n*.yaml`
> | Walk-forward with mined factors completes; report comparable to
> Alpha158 baseline.

### Why the code gap was real

`WalkForwardEngine._run_single_fold` calls
`FeatureDatasetBuilder.build(FeatureDatasetConfig(feature_handler=config.feature_handler, ...))`.
That dispatches through `_FEATURE_HANDLER_REGISTRY`, which at
module-load seeds **only** `"Alpha158"` (per Phase 5's lazy-registration
contract — see `v2-feature-handler-registry`). Without an app-startup
`register_mined_factor_handler(bundle)`, the registry has no
"MinedFactor" entry → `FeatureDatasetBuilderError("feature_handler
must be one of ('Alpha158',), got 'MinedFactor'")`.

Phase 5 documented the bind step in `user_guide.md` ("call this from
your pipeline-startup code"), but **`run_walk_forward.py` is one of
the entry points that needs the bind call** and it didn't have one.
This change adds it.

### Why a separate "compare" CLI

The design doc's success criterion is `"Adding mined factors improves
OOS Sharpe ≥ 10% vs Alpha158-only baseline"`. That comparison is a
two-run, two-output-dir, side-by-side diff. The walk-forward report
schema already carries everything needed
(`mean_information_ratio`, `mean_annualized_return`, etc.). A
dedicated `compare_factor_handlers.py` CLI:

- Avoids polluting the walk-forward engine with two-handler awareness
  (a single run is still one handler).
- Produces a stable JSON manifest (`compare_report.json`) operators
  can grep / paste into a PR description for the bake-off result.
- Is fully testable on synthetic walk-forward reports — no PIT
  bundle required to verify the diff logic.

### What still needs the PIT bundle

The end-to-end bake-off (Alpha158 walk-forward vs MinedFactor
walk-forward on csi300 over the design doc's 2018–2025 window) still
needs:
1. PIT bundle on disk (`inventory.md` §F.3).
2. A Phase 3 miner run producing a real `runs/{id}/` directory.
3. `python -m src.factor_mining.promote --run … --to v1` to produce
   `production/v1/`.

After those three operator steps,
`python scripts/run_walk_forward.py config_walk_mined.yaml` runs the
bake-off and `python scripts/compare_factor_handlers.py
output/walk_forward/walk_forward_report.json
output/walk_forward_mined/walk_forward_report.json --out
output/walk_forward_compare/compare.json` produces the diff. This PR
ships the wiring + the compare CLI; the operator runs the bake-off.

## What Changes

- **MODIFY `scripts/run_walk_forward.py`** — `_load_config`:
  - Accept four new top-level YAML keys (in addition to the existing
    `provider_uri` + `region` + `WalkForwardConfig` field set):
    `mined_factor_pool_dir`, `mined_factor_delisted_registry_path`,
    `mined_factor_pit_provider_uri`, `mined_factor_universe_name_override`.
    The "unknown key → hard error" rule is preserved for any key not
    in this expanded allow-list.
  - `mined_factor_pool_dir` is **required** when
    `feature_handler == "MinedFactor"`; missing → hard error pointing
    at the user guide.
  - `mined_factor_pit_provider_uri` defaults to the top-level
    `provider_uri` (so a single PIT bundle drives both qlib and
    MinedFactor evaluation by default; explicit override warns if
    they diverge).
  - `mined_factor_delisted_registry_path` is **required** when
    feature_handler is MinedFactor.
  - Returns a `RunWalkForwardConfig` dataclass that bundles
    `WalkForwardConfig` + `QlibRuntimeConfig` +
    optional `MinedFactorBundle`. Existing callers that pass YAMLs
    without mined-factor keys continue to work unchanged
    (`MinedFactorBundle` is None).

- **MODIFY `scripts/run_walk_forward.py`** — `main()`:
  - After `init_qlib_canonical(...)` and before
    `WalkForwardEngine.run(...)`, if the parsed config has a
    `MinedFactorBundle`, call
    `register_mined_factor_handler(bundle, replace=True)`.
  - The `replace=True` is important: a subsequent
    walk-forward run in the same Python process (e.g. CI test
    matrix) re-binds the registry slot to whichever pool the new YAML
    points at.

- **ADD `config_walk_mined.yaml`** — operator-facing example:
  - Extends `config_walk.yaml` (so it inherits the qlib / fold
    sizing / model defaults).
  - Sets `feature_handler: "MinedFactor"`, `output_dir:
    "output/walk_forward_mined"`,
    `mined_factor_pool_dir: "research/mined_factors/production/v1"`
    (placeholder for operator to fill in), and
    `mined_factor_delisted_registry_path: ""` (operator-fill
    placeholder).

- **ADD `scripts/compare_factor_handlers.py`** — bake-off comparison
  CLI:
  - `python scripts/compare_factor_handlers.py BASELINE_REPORT
    CANDIDATE_REPORT [--out OUTPUT_JSON] [--metrics LIST]`.
  - Reads two `walk_forward_report.json` files; produces a diff over
    the success-criterion aggregate metrics (`mean_information_ratio`,
    `mean_ic_1d`, `mean_annualized_return`, `worst_drawdown`).
  - Prints a one-line summary (baseline IR vs candidate IR, % delta)
    and exits 0. Optional `--out` writes a richer JSON manifest.
  - No data access; this is a pure-JSON-arithmetic CLI.

- **ADD new capability `v2-factor-mining-walk-forward`** — four
  requirements covering the wiring, the YAML schema extension, the
  required-keys contract, and the comparison CLI's diff contract.

- **MODIFY `v2-feature-handler-registry`** — extend the existing
  "MinedFactor registration is explicit" requirement to acknowledge
  that `scripts/run_walk_forward.py` is now an authorised
  registration site (per `feature_handler == "MinedFactor"` in the
  walk-forward YAML).

## Non-Goals

- **No actual end-to-end bake-off run on this machine.** The PIT
  bundle is not available (`inventory.md` §F.3). The CLI + script
  are verified via synthetic-report unit tests; the bake-off
  itself is an operator action.
- **No edits to `WalkForwardConfig`** — adding mined-factor fields to
  the dataclass would pollute the contract every config consumer
  shares. The mined-factor parameters are top-level YAML keys
  consumed by `run_walk_forward.py` only.
- **No edits to `WalkForwardEngine`** — the engine already supports
  arbitrary feature handlers via the registry; nothing inside the
  engine needs to know about MinedFactor.
- **No new aggregate metrics in the walk-forward report.** The diff
  CLI consumes the existing schema; if Phase 7+ wants new metrics it
  is a separate change.
- **No automatic promotion based on bake-off results.** D4 manual
  gate stands; the compare CLI's role is to *inform* the operator,
  not to replace `promote.py`.
- **No edits to `src/factor_mining/`** — the bind call lives in
  `scripts/run_walk_forward.py` which already imports
  `src.data.mined_factor_handler` (allowed under `src/data/`).
- **No GPU.** Phase 4 skipped.
