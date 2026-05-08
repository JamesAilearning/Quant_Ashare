## 1. OpenSpec Governance

- [x] 1.1 Archive completed active OpenSpec changes and sync their deltas into baseline specs.
- [x] 1.2 Add and validate runtime reliability/provenance spec deltas for this change.

## 2. Walk-forward Provenance And CLI Boundaries

- [x] 2.1 Persist per-fold post-ensemble prediction artifacts and use them as canonical `predictions_ref`.
- [x] 2.2 Record contributing model refs, prediction artifact hash, and index-mismatch rejections in fold reports.
- [x] 2.3 Require exact index equality for ensemble prior predictions before averaging.
- [x] 2.4 Require explicit `provider_uri` in the walk-forward CLI config loader.
- [x] 2.5 Fix comparison CLI treatment ensemble metadata lookup for baseline-only periods.

## 3. Runtime Numeric And Optional-Step Hardening

- [x] 3.1 Forward early-stopping controls to XGB/CatBoost fit calls and enforce CatBoost depth bounds.
- [x] 3.2 Reject duplicate prediction indexes before lag/unstack and sanitize non-finite position values.
- [x] 3.3 Reject non-finite attribution returns/weights and fail Brinson when no instrument returns exist.
- [x] 3.4 Keep completed pipeline backtests/reports when optional factor analysis or chart generation fails.
- [x] 3.5 Catch per-trial optimizer failures without aborting the entire Optuna run.

## 4. Data Boundary Hardening

- [x] 4.1 Preserve original CSV column indexes when benchmark/temporal artifact loaders normalize headers.
- [x] 4.2 Fix Tushare adjusted VWAP zero-volume fallback and reuse a cached Tushare pro client handle.

## 5. Verification

- [x] 5.1 Add targeted regression tests for the changed boundaries.
- [x] 5.2 Run targeted runtime/data tests.
- [x] 5.3 Run `openspec validate --all --strict`.
