## Context

`recommend()` calculates the optional boolean `rebalance_day` and optional `next_rebalance_date` from the established calendar schedule. `write_outputs()` already puts those fields at the JSON top level but not into either CSV. The operator reads JSON; the recommendation publisher moves the three files without parsing CSV columns. Existing writer cadence tests check only JSON and mostly use empty lists.

## Goals / Non-Goals

**Goals:** Faithfully project existing cadence context to nonempty CSV rows, retain machine-readable header-only exports, document empty-file limitations, and preserve legacy daily output.

**Non-Goals:** No new status/action field, schedule calculation, trade authorization, ranking/entry-date change, JSON schema-version change, production rewrite, UI change, Pipeline/WalkForward artifact change, or canonical metric change.

## Decisions

1. Build one cadence dictionary from the result after existing pre-write validation, or an empty dictionary when the marker is None. Reuse it for CSV dataframe column assignment and the JSON top-level update. Do not mutate `buy_rows` (also JSON picks) or `result.scored_frame`.
2. Append `rebalance_day`, then `next_rebalance_date`, to both CSVs only for enabled cadence. Preserve all original columns, order, rows, dates and encoding. Use pandas' existing boolean and missing-cell serialization: True/False and an empty cell for an unknown date. Do not fabricate the next anchor.
3. A header-only CSV cannot carry run-level values. Keep zero rows, append headers only, and direct consumers to the sibling JSON for the actual cadence state. Do not insert metadata as a fake stock or add a nonstandard CSV preamble. A CSV False means HOLD/non-entry; the existing CLI/operator surfaces supply the explicit human HOLD notice.
4. At the serialization boundary, require `rebalance_day` to be None or a real Python bool before any filesystem I/O. Dataclass annotations are not runtime validation, and numeric/string aliases must not be converted through truthiness; the JSON reader already rejects these. Keep existing True/next-date consistency validation; broader date validation is not part of this change.
5. Clarify the full existing generation-context requirement, rather than silently contradict its CSV-unchanged sentence. The new suffix is a cadence projection, not propagation of JSON generation metadata. JSON v2 and pick row fields remain exactly as before; daily CSV bytes remain unchanged.

## Risks / Trade-offs

- External consumers expecting an exact cadence CSV header must tolerate two appended columns → document the additive change; scoped repository search finds no CSV parser requiring code migration.
- Old exported cadence CSVs lack the marker and cannot prove daily behavior → absence is not permission to trade; consult the sibling JSON. No retrospective rewriting.
- Empty tables do not self-describe boolean/date values → state this limitation explicitly, preserving zero stock rows and JSON authority.
- `0 == False` and `1 == True` can defeat equality-based type checks → test numeric/float/string aliases plus actual booleans, before any writes.

## Migration Plan

Only newly generated cadence-enabled CSVs gain the two columns. Daily files and all valid JSON shapes remain compatible. Verify real CSV round trips, nonmutation, exact daily bytes, invalid-marker no-I/O, full tests, independent review and remote review/CI before merge. No production data or deployment actions.

## Open Questions

None for this projection; execution policy and empty-run state remain governed by existing result/JSON contracts.
