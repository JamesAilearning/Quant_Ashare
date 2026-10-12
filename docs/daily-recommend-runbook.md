# Daily recommendation runbook (manual candidate generation)

This is the manual recommendation step. The scheduler runs data updates only;
it must never place orders or automatically run recommendation. A successful
candidate run is not a production-acceptance or future-execution certificate.

## What the list means

Schema v3 uses the latest closed, recorded price session **T** and a separate
Tushare SSE trading-arrangement snapshot to plan **T+1**. It never creates
future price bars or reads entry-day OHLCV to choose candidates.

- Features, scores and the canonical microstructure exclusion use data through
  T only. Current ST names and explicitly qualified data-quarantined securities
  are excluded before Top-K.
- Every row has `eligible_as_of` describing known candidate eligibility at T.
  Every row's `tradable_flag` is **null**, including excluded audit rows:
  future suspension, limit conditions and actual execution remain unknown.
- The run declares `recommendation_mode="next_session_candidate"` and
  `entry_tradability_status="unconfirmed"`. A human checks entry-day conditions,
  portfolio/risk limits and sizing before any trade. There are no broker orders.
- N5 remains anchored on decision T and the recorded price calendar. A HOLD
  result is monitoring only, not a rebalance instruction. A missing next N5
  anchor stays null; the planned entry date must not replace it.
- Schema v2 and older files remain historical/read-only. They do not become
  current candidates. A v3 candidate is not a holding or an executed rebalance,
  and blocks nominal-baseline scanning rather than falling through to an older
  list. Historical nominal-list comparisons are not actual holdings.

This is an explicitly approved serving-timing change. It does not change
training, model/ensemble weights, the stock pool, Top50, N5 anchoring, holdings,
execution logic or the single canonical qlib metrics path. Candidates are not
new backtest or performance evidence.

## Separate schedule acquisition

Recommendation itself performs no online fetch. Acquire an SSE schedule with
the existing canonical Tushare client using an explicit bounded range and a
**new** output path:

```powershell
python scripts/fetch_recommendation_calendar.py --start-date 20260101 --end-date 20261231 --output D:/qlib_data/recommendation_calendar_20261012.json
```

This example's range is explicit, not a rule for deriving exchange holidays.
The command makes one `trade_cal` client call for
`exchange,cal_date,is_open`, validates one row per calendar day over the entire
requested range, and binds source, query, acquisition time and the same bytes'
SHA-256. It never touches the raw update manifest, qlib price calendar or bins.
A transport/client failure is not retried by this command or the canonical
client. No new retry loop is added around the vendor SDK.

Snapshots must be ordinary single-link files, at most 1 MiB, with strict JSON
fields and integer 0/1 open markers. Query spans are limited to three years.
Missing days, duplicate keys/dates, wrong exchange, unknown fields, non-finite
JSON, stale/future timestamps and file-read races refuse. Publication never
overwrites an existing file; failed/old evidence is not deleted to retry.

Set `QUANT_TRADING_CALENDAR` for both CLI and the UI, or pass
`--trading-calendar` explicitly to the CLI. The fallback path is
`D:/qlib_data/recommendation_calendar.json`; **no bundled/default calendar
content or weekday guess is supplied**. Missing/unreadable input refuses.
Keep each acquired snapshot immutable. To refresh, acquire a new filename,
review its receipt, then deliberately change the deployment configuration.
A snapshot is accepted for at most seven elapsed days; no automatic refresh is
added by this change.

## Update first, then recommend

1. Refresh the separate arrangement snapshot when needed.
2. Complete and verify a genuine canonical data update through the latest
   closed session. Read that update's report, native exit and final provider
   result; a stale status file or successful recommendation does not prove the
   update ran.
3. Generate candidates manually using that provider, the explicitly selected
   model/ensemble and the arrangement snapshot.
4. Read the candidate date, HOLD/rebalance status and all incomplete-data
   warnings. Check actual entry-day conditions at trade time.

The serving clock is timezone-aware Shanghai time. An open day is conservatively
treated as closed only at **15:30 +08:00**. Before that cutoff the previous
scheduled open session is the latest closed one; holidays/weekends come only
from the source snapshot. A 15:30 clock check does not prove vendor bars are
final or that the update started after close. Production recovery must
separately verify a real update fetched after the approved cutoff.

By default the provider tail must equal that latest closed session. A lagging
tail refuses, even within the old 14-day freshness tolerance. An intraday/future
tail also refuses, even for an explicit historical request. All price sessions
inside the schedule overlap must match it exactly. The snapshot must cover the
clock date, T and a known next open session.

For example, on Monday before the cutoff with Friday recorded, T is Friday and
entry is the source-announced Monday, without any Monday price bars. After
Monday's cutoff, default generation requires completed Monday prices and plans
the next source-announced open session. Re-running does not turn the earlier
candidate into an execution record.

## Commands

```powershell
# Existing single-model defaults, with an explicit arrangement snapshot:
python scripts/daily_recommend.py --trading-calendar D:/qlib_data/recommendation_calendar_20261012.json

# Current approved ensemble layout: select the model explicitly.
python scripts/daily_recommend.py --ensemble-manifest D:/stock/phase_b_artifacts/csi800_n5_ensemble_manifest.json --instruments csi800 --topk 50 --rebalance-cadence-days 5 --trading-calendar D:/qlib_data/recommendation_calendar_20261012.json
```

Use the script entry point rather than `python -m`; its main guard and
`freeze_support()` are required on Windows. The ensemble manifest is never
implicitly selected for writing, even when the UI knows the incumbent.

If the provider carries an approved quarantine, supply the **matching explicit**
`--suspension-quarantine` policy, follow the
[security-anomaly isolation runbook](operations/security-anomaly-isolation.md)
or the [688766-specific runbook](suspension-quarantine-runbook.md) for that
specific policy, and retain its
incomplete-data disclosure. The bounded `suspend-security-anomalies-v1` policy
is not permission to ignore arbitrary missing APIs, calendar defects, model
problems or corrupt evidence. `--allow-holey-recommend` does not authorize a
structured quarantine. Existing UI buttons do **not** supply quarantine
authorization; use the reviewed CLI for that case, and use UI to read results.
This timing change adds no new UI authorization.

A specific historical T is allowed only when it is a real recorded closed
session covered by the snapshot:

```powershell
python scripts/daily_recommend.py --as-of 2026-10-08 --topk 30 --trading-calendar D:/qlib_data/recommendation_calendar_20261012.json
```

This recalculates candidates using the **current** ST/name source; it is not a
historical PIT tradability or performance reconstruction. It cannot be shown
as current when T is not the latest closed session.

| Flag | Default | Meaning |
|---|---|---|
| `--as-of` | latest closed, recorded session | Decision/data cutoff T, not the penultimate price day. |
| `--trading-calendar` | `QUANT_TRADING_CALENDAR` or the path above | Separate authenticated arrangement snapshot; no online fallback. |
| `--topk` | `50` | At most K eligible candidate names, not K confirmed tradable stocks. |
| `--rebalance-cadence-days` | single-model `1`; ensemble's pinned serving configuration | Existing daily or approved N5 `5`; decision-T anchor unchanged. Explicit ensemble values must match the pinned configuration. |
| `--instruments` | `csi300` | Must match the selected model/ensemble. Production CSI800 remains explicit. |
| `--out-dir` | `output/daily_recommend` | JSON and two CSV outputs. |
| `--bundle-max-age-days` | `14` | Coarse extra safeguard; cannot bypass latest-closed default equality. |
| `--st-max-age-days` | `7` | Existing current-ST/name freshness guard. |
| `--allow-holey-recommend` | off | Research-only broad incomplete-fetch override, not production approval. |
| `--suspension-quarantine` | unset | Existing explicit policy with matching bounded evidence and exclusions. |

Model/provider/name/registry/fit-window path flags remain available. CLI path
flags override environment defaults; see
[operations-env-vars.md](operations-env-vars.md). Do not relax guards to make
a live stale or incomplete bundle appear healthy.

## Files and current display

Three files are stamped by T: `daily_recommendation_<T>.json`, the candidate
`.csv` and `_scored_full.csv`. Existing filenames do not imply an executable
buy list.

- JSON is schema **3**. It retains model/provider provenance and adds
  `meta.trading_calendar` (path, SHA-256, source API, exchange, exact query,
  fetched_at) plus `meta.latest_closed_session`.
- Counts are `n_scored` (eligible pool), `n_masked` (known T microstructure
  exclusions), `n_st_excluded` and `n_quarantined` (always present, including
  zero). Their disjoint sum equals the scored audit's row count, not the stock
  pool size; NaN scores are logged and dropped before that audit.
- Pick/audit rows contain `as_of_date, entry_date, stock_code, stock_name,
  predicted_score, eligible_as_of, tradable_flag, unavailable_reason`;
  picks also carry contiguous `rank`.
- Both CSVs repeat mode and unconfirmed entry status. Existing N5 fields and,
  when active, quarantine disclosure columns remain. Null CSV entry tradability
  is blank, not false. Empty CSVs keep headers and zero stock rows; the sibling
  JSON carries the run context.
- `unavailable_reason` reports known candidate exclusions only:
  `data_quarantine`, `st`, `suspended`, `one_price_lock` or
  `unavailable`. Quarantine takes priority; otherwise known T microstructure
  takes priority over current ST. It never certifies T+1.
- The writer validates mode, dates, provenance, strict eligibility/null status,
  ranks, finite scores, counts and exact audited Top-K before creating output.
  Direct output writes still are not an atomic multi-file transaction; the
  operator runner retains its existing private-directory publication boundary.

The UI cross-checks the incumbent model/provider identity and the **configured**
calendar path/SHA, not an arbitrary artifact-supplied file. Current status also
requires decision T equal to the current provider tail and recorded latest
closed session, a nonfuture generation timestamp and a fresh calendar. The
candidate expires at the planned entry day's 15:30 cutoff. Wrong identity,
missing/changed source, invalid v3 or expiry disables new decision/review/queue
actions while keeping history and existing journal entries readable. A matching
current candidate still requires human execution checks.

## Refusals and safe recovery

| Refusal | Operator response |
|---|---|
| Missing, stale, future or malformed arrangement snapshot | Acquire a new exact bounded snapshot; review and configure it. Never infer weekdays or extend price bins with future dates. |
| Provider tail is not the latest closed session | Complete the genuine data update; do not raise the 14-day tolerance or relabel historical replay as production. |
| Provider contains an intraday/future session | Stop and inspect update timing/provider provenance; do not force `--as-of` around it. |
| Price and arrangement sessions disagree / no known next open | Inspect real source evidence and range; never guess an entry date. |
| ST/name source missing, stale or inconsistent | Refresh the canonical source and inspect its embedded snapshot date. |
| Missing/corrupt/incomplete fetch evidence | Repair the source or use only an already approved bounded quarantine with its explicit disclosure; do not silently waive unrelated holes. |
| Model universe, fit window or ensemble mismatch | Correct the explicit model inputs; do not silently choose a different model. |
| v3 writer validation failure | Preserve evidence and fix the producer/callers. Never downgrade to schema v2 to publish. |

For production recovery, candidate validation is only one gate. Preserve
independent fidelity/PIT evidence, same-point rollback backups, reviewed
reversible fixed-path cutover and a first genuine post-cutoff production update.
Restore the data scheduler last. Never overwrite a held raw root/registry lock
inode or use isolated success as proof of a production execution.

Rollback of this release restores the prior reviewed code/configuration and
serving evidence; do not rewrite old JSON/CSV in place or label a legacy v2
historical list as a current candidate. The buy/sell decision remains human.
