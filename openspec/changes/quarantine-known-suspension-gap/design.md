## Context

The 2026-09-22 isolated attempt (not production) lost eight known business keys
for 688766.SH, confirmed by six successful narrow vendor queries. Original
evidence is frozen under `production_recovery_20260922/full`. The user approved
isolation on 2026-09-23, not fabricated history or a general partial-data bypass.

## Goals / Non-Goals

**Goals:** keep daily serving available for other validated securities through
one explicit, auditable incident policy; preserve default refusal and full history.

**Non-Goals:** model/feature/index changes, automatic holdings management, a
general blacklist, percentage-based tolerance, editing old recovery generations,
automatic production switch, or granting historical performance certification.

## Decisions

1. A standard-library-only contract module declares exactly one policy ID,
   `suspend-688766-20251127-20251209`. Its keys are 688766.SH with null timing,
   S on 20251127/20251128/20251201/20251202/20251203/20251204/20251205 and R on
   20251209. Default config is None; unknown IDs fail before side effects.
   Add `suspension_quarantine` / `--suspension-quarantine` explicitly to fetch,
   daily update, build and serving, never translate it to either broad hole override.

2. The fetcher continues to validate full responses/partitions and all retained
   keys. Under this policy only, a loss subset of these eight keys can be published
   as the exact new vendor response, never unioned with retained rows. Conflicting
   replacements at an affected date, other missing keys, incomplete query range,
   invalid/corrupt data or unavailable evidence refuse publication. A reference
   snapshot containing all eight keys must exist before the first quarantine;
   subsequent retries reuse and verify that content-addressed reference. Missing
   approved keys are checked against the reference on EVERY refresh, not just
   against the already-shortened latest file. Schema-bearing empty queries cannot
   erase unrelated history. Restore only when every approved key is again returned.

3. Preserve the retained and reference Parquets under
   `_suspension_quarantine/<sha256>.parquet` without replacing existing evidence.
   Hash and prepare the candidate before replacing `suspend_d.parquet`. Before
   the data rename, durably record a pending publication journal binding old/new
   raw bytes, the prior manifest, the intended quarantine or full recovery, and
   the query interval. Ordinary manifest readers/builders and reset refuse while
   pending, including missing-manifest and broad-hole-override cases. The final
   manifest writer first binds its intended byte hash in the journal, fsyncs and
   replaces the manifest, verifies both published hashes, then clears pending.
   Only an explicit matching, non-dry suspension refresh may recover: an already
   committed pair needs journal cleanup; an uncommitted candidate is preserved
   as evidence and rolled back to the saved prior bytes before mandatory refetch.
   Unknown bytes, corrupt journals or insufficient scope fail without mutation.
   Candidate bytes are fsynced before journal publication. This protocol covers
   process interruption and write failure; it does not claim arbitrary device or
   filesystem power-loss atomicity for directory/link/rename operations.
   Append one `FetchHole(endpoint='suspend_d', unit='file',
   reason_class='quarantined_history')` carrying optional typed `quarantine`
   metadata: policy_id, missing_dates, reference_sha256, retained_sha256,
   candidate_sha256, query_start_date, query_end_date. Preserve it through manifest
   merge/read/write and bundle stamp read/write. Missing metadata, wrong reason,
   unsupported keys or malformed hashes/dates fail loud. Legacy holes stay byte
   compatible. A manifest or stamp containing quarantine uses schema v2; v1 must
   not carry quarantine and v2 must carry it. New readers accept both shapes; old
   readers refuse v2 instead of silently dropping the new safety contract. Clean
   and ordinary-hole outputs remain v1. Never mark quarantined data `complete` or
   `built_from_holey_fetch=false`.

4. Raw fetch keeps exit 3 while quarantine remains. Daily update may continue only
   with explicit matching policy, exactly that one structured hole, trusted raw
   evidence/hash and all other required coverage. Build independently repeats the
   gate and evidence verification. A broad hole override cannot bypass malformed
   quarantine evidence or disable isolation. Coverage anchors still derive from
   complete core inputs. Default gates refuse the incomplete bundle as before.

5. Serving separately opts into the same policy and validates the stamped evidence
   structure. It excludes SH688766 before Top-K, leaves all model scores and
   original instruments untouched, retains an audit row with
   `unavailable_reason='data_quarantine'`, and replenishes from the full eligible
   ranking. A separate count avoids misclassifying quarantine as ST or suspension.
   JSON metadata and both CSVs disclose policy/security and incomplete-data status;
   CLI warns even when the security was not scored. No input/output represents an
   existing position as sold. Removing the flag while the hole remains refuses.

6. Pipeline and WalkForward reject a bundle with this quarantine at their entry
   boundaries, before runtime/output work. The direct frozen-model OOS and retrain
   qualification CLIs also refuse before scoring; the shared BacktestRunner
   refuses before simulation so callers cannot bypass the engine entry checks.
   They never apply today's isolation to the historical universe or silently
   qualify a model rotation against incomplete data. No shared report fields change. Existing certified
   results and models remain unchanged. A fresh complete fetch + rebuild removes
   quarantine; prior evidence remains on disk for audit.

## Risks / Trade-offs

- A single file hole otherwise hides scope -> one strict shared metadata contract,
  candidate/reference/retained byte binding and negative mixed-hole tests.
- A second update could forget an erased key -> always compare against the original
  complete incident reference, and force actual refresh instead of blind resume.
- JSON/CSV could hide the isolation -> validate the writer boundary, explicit
  exclusion count and context in all outputs, including empty lists.
- Operator readers could hide the exception -> show it on current and historical
  baseline artifacts independently, reject malformed disclosure, and never
  advertise the broad hole override as authorization for this incident.
- Crash between data and manifest publication -> durable pending state blocks all
  readers; hash-bound recovery preserves the interrupted candidate and original
  reference, requires an actual refetch, and also covers full restoration to v1.
- This is a known-incident policy, not proof of vendor-wide completeness; no extra
  tickers or dates are inferred safe. Other data failures remain blocking.

## Migration Plan

Synthetic RED/GREEN tests, serial full gates, imports, strict OpenSpec validation,
independent local review, PR and fresh Codex review before merge. After merging,
use a NEW isolated recovery root and version-pinned helpers to validate qualified
data, exclusion and ordinary refusal paths. Reversible publication requires fresh
backups and supervised first update; the scheduler remains disabled until then.
