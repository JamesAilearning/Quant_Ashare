## Context

`TushareFetcher._fetch_stock_basic` writes current `ts_code`, `name`, and embedded `snapshot_date` into active_stocks.parquet. `recommend` already reads it once and checks required columns and date consistency, but `_name_map_from_df` coerces raw values and silently overwrites duplicate keys; missing keys become empty strings. The shared ST predicate correctly accepts only known ST markers, not a missing-evidence contract.

## Goals / Non-Goals

**Goals:** Refuse incomplete or ambiguous current-ST identity for every otherwise eligible scored candidate, with synthetic runtime-path coverage.

**Non-Goals:** No historical-ST inference, new inactive-stock selection rule, score validation redesign, current-ST predicate changes, cadence or HOLD changes, artifact schema changes, production data repair, or competing official metrics.

## Decisions

1. Validate at the recommendation boundary, after the authoritative entry-day mask and existing snapshot guards, before name coercion, ST filtering, and Top-K. A small pure helper consumes the same already-read dataframe and required ts_code set. It raises `DailyRecommendationError` with missing/duplicate/invalid-name code diagnostics and a source refresh instruction; no second file read or implicit repair.
2. The required set is the entire non-NaN score map minus entry-day `masked_pairs`, translated through existing `qlib_to_ts_code`. Checking only final picks misses unknown names elsewhere in the tradable audit pool. Checking every provider or scored row falsely blocks stocks already guaranteed unavailable by the canonical mask (including past delistings).
3. Required codes must have exactly one row whose ORIGINAL name is a string with non-whitespace content. Do not stringify null/numeric values or choose first/last duplicate. Reject even duplicate identical names because unique evidence is the contract. Invalid unrelated or already-masked rows do not change selection; their existing best-effort audit display remains unchanged.
4. Keep all existing whole-snapshot guards even when every score is masked. Keep ordinary valid-name ranking, ST marker recognition, microstructure reason precedence, counts, result/CLI interfaces and HOLD behavior unchanged. Do not turn unknown status into a new exclusion reason or silently shrink the selection universe.

## Risks / Trade-offs

- Previously accepted partial snapshots will refuse → error points to the configured name source and affected codes; operator refreshes through the existing data workflow.
- A historically tradable but currently delisted candidate may lack a current name → explicit refusal is faithful to the current-snapshot contract; substituting historical names is a separate policy, not part of this fix.
- Helper-only tests can pass while the runtime call is missing/misordered → mock only expensive IO/model/provider seams and exercise `recommend`, actual snapshot reading/validation, ST filtering and Top-K on synthetic inputs.
- Type coercion can conceal malformed values → cover None, NaN, pandas.NA, empty/whitespace, numeric/boolean names and duplicate ordering rather than only different valid strings.

## Migration Plan

No persisted migration or live data rewrite. Merge only after local regression/full suites, independent reviews, latest-head Codex review and CI. Any rollback is an explicit code revert; do not bypass failure by relaxing freshness or inventing names.

## Open Questions

None for this bounded completeness fix; broader historical/forward execution policy remains unchanged.
