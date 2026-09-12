## Context

The existing single-file aggregate path checks prior manifest bounds, makes one
network request, and overwrites the Parquet atomically. This permits an observed
5,000-row slice to erase a different retained 5,000-row slice. The independent
provider acceptance checks cover price-prefix fidelity but not these raw keys.

## Goals / Non-Goals

**Goals:** prevent observable truncation or history loss in `namechange` and
`suspend_d`; publish only a fully collected, validated candidate; keep existing
range/provenance safeguards, stable failure units and manifest coverage rules.

**Non-Goals:** other endpoints, stock-universe selection, changing raw schemas,
pagination fallback, automatic legacy-data repair, model/trading semantics,
deploying a provider or declaring all vendor events independently verified.

## Decisions

1. For suspensions, use sequential calendar-month windows clipped exactly to
   requested bounds. At a conservative 5,000-row raw-response tripwire, bisect
   the date window without gaps or overlap. A saturated single day is a failure.
   For name changes, preserve the existing one-request range but reject responses
   of at least 10,000 rows. Do not substitute unverified date partitioning or
   offset pagination: missing announcement dates may be excluded server-side.
   Check counts before deduplication. These tripwires reproduce observed unsafe
   responses, not a guarantee of permanent vendor limits.

2. Validate the exact requested field set and real date strings. The official
   [namechange documentation](https://tushare.pro/document/2?doc_id=100) defines
   query bounds as announcement dates (`ann_date`); its output `start_date` is
   the effective date and must not be used to partition or filter the response.
   [suspend_d](https://tushare.pro/document/2?doc_id=214) uses `trade_date`.
   Missing suspension dates, invalid non-null dates and out-of-window responses
   fail explicitly. Preserve nullable announcement and end dates in name history;
   do not discard rows, substitute effective dates, or guess null dates.
   A schema-bearing empty window is valid; a schema-less response is not.

3. Protect source business keys: all four suspension columns; for name changes,
   all requested fields except `end_date`. This permits a real end-date update
   but not disappearance, changed announcement provenance, or loss of another
   name with the same effective date. Preserve every old key and every observed
   saturated-parent key in the successful candidate. Remove only exact duplicate
   rows; conflicting payloads for one key fail rather than taking the last row.
   Do not concatenate old data into the candidate as a fallback. Reject malformed
   retained data explicitly; unknown manifest provenance keeps its existing
   pre-request hard-abort behavior.

4. Collection is a pure, bounded helper called by the existing fetch producer.
   All API calls still pass through `_safe_call`, unchanged rate limiting and
   retry classification. Named limits bound each endpoint to 2,000 partition
   calls (each retaining existing bounded retries), 1,000,000 accepted rows,
   and 256 MiB of accepted DataFrame deep memory. Saturated responses are also
   checked against the memory budget before processing. Budget failures do not
   truncate results or trigger an alternate strategy. No parallel network work.

5. The only publication is the existing atomic write after collection and key
   preservation succeed. A response/preservation/budget defect is a recorded
   `unusable_response` hole with stable `unit="file"`, zero files written and
   no advance of retained manifest coverage. Existing transient errors keep
   their original classifications; auth/permission/parameter errors still abort.
   No DTO or disk-schema migration is introduced.

## Risks / Trade-offs

- More API calls and memory than the unsafe single request: bounded, serial
  collection and explicit failures keep load predictable. Exact duplicate rows
  are harmless, but deduplication never masks the saturation check.
- Vendor corrections outside `end_date` can now block refresh: intentionally
  require inspection rather than authorizing arbitrary historic mutation.
- Missing announcement dates or undocumented vendor omissions: fail on visible
  invalid responses; retain the limitation that no API total-count evidence
  proves unobserved history. A real isolated rebuild and source comparison remain
  required before production acceptance. Do not label a synthetic test as that
  operational proof.
- Name changes may be safely blocked rather than fully repaired by this change:
  official docs do not guarantee how null announcement dates enter date queries.
  Official-repository user reports [#1858](https://github.com/waditu/tushare/issues/1858)
  and [#1901](https://github.com/waditu/tushare/issues/1901) describe omissions;
  they are risk evidence, not vendor-confirmed behavior of the current account.
  A per-security, unbounded-name-history strategy needs a separately decided
  historical security set and isolated validation; it is not a silent fallback.
- Existing tests use invented `old_history`/`new_history` columns or fixed one-call
  assumptions: migrate successful paths to real schemas and date-aware responses.

## Migration Plan

Reproduce the loss synthetically before implementation. Run targeted acquisition
tests, required logic/governance tests, source imports, lint/type checks and
OpenSpec validation serially. Review locally to convergence, publish a focused
PR, request Codex review after every push, and merge only after clean evidence.
Keep production task disabled and evidence intact. Any isolated rebuild,
publication, rollback or scheduler restoration is a separate operational gate,
not an automatic side effect of merging this change.
