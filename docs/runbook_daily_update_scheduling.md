# Runbook — scheduling the daily data update (阶段5 PR-P)

This runbook covers **automating the daily *data update*** (`scripts/daily_update.py`)
via the Windows Task Scheduler. It is a **manual setup + one-week supervised trial**,
not an auto-applied change.

**Scope — what is and is NOT scheduled:**

- **Scheduled:** `daily_update` only — fetch → snapshot → rebuild → validate → atomic
  swap of the live qlib provider bundle.
- **NOT scheduled (deliberate):** `daily_recommend`. The morning stock list is produced
  **by hand** after you've eyeballed that the overnight update succeeded (see
  [Each morning](#each-morning)). It never runs unattended.

## Safety properties this relies on

- **Single-flight (exit 17).** The CLI takes process-exclusive **OS advisory locks** on
  every mutable input (the provider dir, the tushare dump, and the registry — each gets a
  sibling `<path>.daily_update.lock`) before any mutation. Two runs that share ANY of them
  — a manual run while the scheduled one is going, a hung run when the next day fires, or
  even a second run with a different `--provider-dir` but the same `--tushare-dir` — are
  serialized: the second exits **17 (`EXIT_ALREADY_RUNNING`)** and touches nothing. The
  kernel owns the locks and releases them when the holder exits — including on a crash or
  kill — so there is no stale lock to clear and no PID-reuse wedge. `--dry-run` is exempt
  (it mutates nothing).
- **Trading-calendar gate (exit 0).** A weekend run no-ops cleanly without churning the
  bundle (PR-O). Weekday holidays fall through to a normal run whose fetch/freshness
  gates no-op on a day with no new bar — handled, never wrongly skipped.
- **Crash-atomic swap.** The live bundle is replaced by two atomic renames; a crash
  mid-swap is completed (or rolled back) on the next run's Stage-0 repair, which runs
  even on a closed day.
- **Fail-loud exit codes.** Every failing stage maps to a distinct exit code (see below),
  so a scheduled run's exit code tells you exactly where it died.

## Prerequisites

- The five operational env vars are set (see [docs/operations-env-vars.md](operations-env-vars.md)).
- A live bundle already exists at `--provider-dir` (the first build is supervised, not
  scheduled — see the 阶段1 runbook history).
- The repo's Python environment is on `PATH` (or use the venv's `python.exe` absolute
  path in the task action).

## Register the scheduled task (Windows)

The full `daily_update` invocation is too long and too quote-fragile for `schtasks /TR`
(a 261-char limit), so **use a wrapper `.bat`** as the task action.

1. Save a wrapper, e.g. `D:\qlib_data\run_daily_update.bat` — edit the paths; keep
   `--start-date 20180101` (the bins build has no range filter, so omitting it re-fetches
   pre-2018 and widens the calendar):

   ```bat
   @echo off
   if not exist "D:\qlib_data\logs" mkdir "D:\qlib_data\logs"
   "C:\path\to\python.exe" "D:\stock\Claude\qlib_trading_system_v2\scripts\daily_update.py" ^
     --tushare-dir D:\qlib_data\tushare_raw ^
     --provider-dir D:\qlib_data\my_cn_data_pit ^
     --delisted-registry D:\qlib_data\tushare_raw\delisted_registry.parquet ^
     --reference-cases D:\stock\Claude\qlib_trading_system_v2\tests\pit\reference_cases.yaml ^
     --start-date 20180101 >> "D:\qlib_data\logs\daily_update.log" 2>&1
   ```

2. Register the task pointing at the wrapper (a short `/TR`, well under the limit). Run
   once in an elevated shell:

   ```bat
   schtasks /Create /TN "QuantAshare\DailyDataUpdate" /TR "D:\qlib_data\run_daily_update.bat" /SC DAILY /ST 20:30 /RL HIGHEST /F
   ```

The `.bat` appends to a single rolling log; the app's own `setup_logging()` also writes
per-run detail. (Avoid `%date%` in the filename — it is locale-dependent and often yields
an invalid name.)

**Schedule time — after the data is published.** A-share EOD data lands a few hours after
the 15:00 close. Run **late (≈20:30+)** with the default `--end-date` (today). If a run
fires *before* today's bars are published, the pre-close freshness gate (PR #271) records
a systemic shortfall and **refuses the build — no swap, live bundle untouched** (a safe
no-op, exit 12); the next day catches up. Scheduling late avoids the wasted run.

## Monitoring — exit codes

| Code | Meaning | Action |
|---|---|---|
| 0 | success (incl. weekend calendar-gate no-op; a weekday holiday runs normally and also exits 0 when there is no new bar) | none |
| 2 | config / setup error (incl. an unwritable / unreachable lock path) | fix args; check `--provider-dir`'s parent is writable |
| 10 | unrepairable bundle state | investigate `.bak`/`.new`; manual repair |
| 11 | fetch failed hard | check tushare token / network; re-run |
| 12 | fetch holes (or pre-close, no `--allow-holey-fetch`) | usually transient (pre-close) — re-run later/next day |
| 13 | snapshot not refreshed to run date | re-run after data publish |
| 14 | rebuild failed | inspect 02/05/03/04 logs |
| 15 | validation failed (06 on the staged bundle) | inspect validation; bundle NOT swapped |
| 16 | swap failed | check disk/permissions; Stage-0 repair on next run |
| **17** | **another run holds the single-flight lock** | **expected if runs overlap; the OS releases the lock when that run exits (even on crash) — no manual clearing** |

`schtasks /Query /TN "QuantAshare\DailyDataUpdate" /V /FO LIST` shows the **Last Result**
(the exit code) and last run time.

## Each morning

After confirming the overnight update succeeded (exit 0 + a recent bundle), produce the
list **by hand**:

```sh
python scripts/daily_recommend.py        # no overrides; clean-fetch bundle
# -> output/daily_recommend/daily_recommendation_<as-of>.{csv,json}
```

`daily_recommend` has a 14-day freshness floor, so a missed update surfaces as a refusal
rather than a stale list.

## Rollback

The previous live bundle is kept at `<provider>.bak` after every successful swap (cleared
at the start of the next swap). To roll back manually, stop any running update, then
rename `<provider>` aside and `<provider>.bak` → `<provider>`.

## One-week supervised trial

Before trusting the task unattended, run it for **one week** and each day verify: the
task's Last Result is 0, the bundle's calendar tail advanced to the expected trading day,
and (after a manual `daily_recommend`) the list is sane. Only after a clean week leave it
unattended. Disable with `schtasks /Change /TN "QuantAshare\DailyDataUpdate" /DISABLE`.
