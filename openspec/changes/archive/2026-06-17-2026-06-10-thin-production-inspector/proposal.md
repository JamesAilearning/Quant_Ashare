# Proposal: thin-production-inspector

## Why

U3 retired the UI's tushare ingest path with the promise of a THIN read-only
replacement: the operator still needs to see whether the production bundle is
trustworthy (was its fetch complete? is it fresh? do the PIT checks pass?)
without the UI ever building data again. And the five operational env vars
lived only in scattered code comments — including a stale `${QLIB_PROVIDER_URI}`
in CLAUDE.md that exists nowhere in the code.

## What Changes

- **New UI page `数据检视` (`web/operator_ui/pages/data_inspect.py`)** —
  read-only inspector of the PRODUCTION bundle: the P3-4c fetch-integrity stamp
  (clean / holey + the holes / missing / corrupt, each with the operator
  consequence), the bundle-health summary (FU-8 machinery), and an on-demand
  thin run of the 06 PIT validator rendered as a checks table. Copy states
  explicitly it INSPECTS production data; bundles are made by the pipeline.
- **Governance test** (`tests/governance/test_data_inspect_readonly.py`):
  source-level red line — no write-side filesystem API anywhere in the page,
  no import of builder / fetcher / orchestrator machinery, the 检视生产 + 只读
  copy present, and the page registered in the navigation.
- **`docs/operations-env-vars.md`** — the five operational env vars
  (`QUANT_PROVIDER_URI`, `QUANT_MODEL_PATH`, `QUANT_DELISTED_REGISTRY`,
  `QUANT_NAME_SOURCE`, `TUSHARE_TOKEN`): consumers, defaults, precedence;
  PowerShell `$env:` spelling (incl. persistent scope); the `${…}` trap
  (loader-level substitution vs PowerShell's own `${…}`).
- **CLAUDE.md** — the stale `${QLIB_PROVIDER_URI}` example replaced with the
  real `${QUANT_PROVIDER_URI}` (+ `:-default` form) and a pointer to the doc.
- **01–06 path residue**: P3-6a Step 0 verified all six pipeline scripts are
  pure-argparse with explicit path args (no env coupling) — nothing to change;
  recorded here as the closing audit.

## Non-Goals

- No new validator checks (the 06 list is untouched; the page only renders it).
- No scheduling, no bundle building from the UI (that is the point).
- No new env vars.
