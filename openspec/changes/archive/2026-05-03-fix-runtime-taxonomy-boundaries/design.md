## Overview

Fix four related boundary defects in one scoped change:

1. risk-constraint execution must not live behind the canonical `src.core`
   import path;
2. Pipeline attribution may use a real taxonomy only when the operator supplies
   an explicit, validated artifact boundary;
3. shipped Tushare utilities need project metadata for their dependency; and
4. static taxonomy publishing must fail before IO when duplicate instruments
   would make the intended map consumer reject the artifact.

## Decisions

### Risk Constraints

- Move the existing implementation to `src.experimental.risk_constraints`.
- Replace `src.core.risk_constraints` with a small compatibility module that
  raises `RiskConstraintError` on execution and points callers at the
  experimental namespace.
- Update existing behavior tests to import the experimental module and add a
  governance regression that the canonical core path fails closed.

This preserves the experimental code for future decision-first migration while
removing its active canonical runtime surface.

### Pipeline Taxonomy Attribution

- Add optional `PipelineConfig` fields:
  - `industry_artifact_path`
  - `industry_manifest_path`
  - `industry_taxonomy_id`
  - `industry_temporal_mode` (only `static` supported in Pipeline attribution)
- Require artifact path, manifest path, and taxonomy id to be supplied together.
- When configured, load the artifact through `TaxonomyArtifactLoader`, validate
  it through `TaxonomyDataContract`, require no contract errors, require the
  manifest taxonomy name to match `industry_taxonomy_id`, and only then call
  `load_industry_map`.
- Pass the resulting map and taxonomy id into `AttributionConfig`.
- When no taxonomy is configured, keep the existing board heuristic behavior
  and taxonomy label.

### Tushare Dependency Metadata

- Add a `tushare` optional extra in `pyproject.toml`.
- Update runtime error/install hints to mention installing the project extra.

### Duplicate Static Taxonomy Rows

- Add a pre-IO duplicate-instrument validation step in
  `TaxonomyArtifactPublisher.publish` for `temporal_mode="static"`.
- Keep trade-date and range modes unchanged because repeated instruments can be
  valid across time.

## Non-Goals

- Do not make risk constraints canonical or wire them into Pipeline official
  metrics.
- Do not support temporal/range taxonomy maps in Pipeline attribution yet.
- Do not make Tushare a mandatory dependency for core installs.
- Do not change qlib official backtest semantics.

## Validation

- Focused tests for risk boundary, Pipeline taxonomy config and attribution
  config construction, duplicate static taxonomy rejection, and Tushare install
  hint.
- `openspec validate --all --strict`.
- `pytest -q -p no:cacheprovider tests/governance tests/logic`.
