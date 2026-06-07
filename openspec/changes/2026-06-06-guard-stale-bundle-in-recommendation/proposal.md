# Proposal: guard-stale-bundle-in-recommendation

## Why

`recommend()` resolves the as-of decision date from the qlib **bundle's own
calendar** (`resolve_dates` → `D.calendar()`). That makes it blind to its own
staleness: if the data pipeline (tushare fetch → qlib bin rebuild) has not run
and the bundle ends weeks/months ago, `resolve_dates` happily picks the
bundle's last day as "today", builds Alpha158 features on those stale prices,
and emits a normal-looking buy list — with **no error or warning**.

This is the daily-automation worst case: once `recommend()` runs unattended on
a schedule, a single failed data update would silently produce a stale list
every day. The path already fails loud on a stale ST snapshot
(`_validate_st_snapshot`, ≤7d) and on look-ahead (`assert_no_lookahead`), but
there is **no equivalent guard for the PRICE/FEATURE data freshness**. This
adds it — the prerequisite for safely automating the pipeline (ops Phase 2,
before the data-update chain Phase 3 and scheduling Phase 4).

## What Changes

- `src/inference/daily_recommend.py`:
  - `_bundle_is_stale(bundle_last_day, today, max_age_days)` (pure predicate) +
    `_assert_bundle_fresh(...)` (raises `DailyRecommendationError` if stale,
    with an actionable message).
  - `recommend(config, *, now=None)`: after `resolve_dates`, compare the
    bundle's last trading day (`D.calendar()[-1]`) against an EXTERNAL `today`
    (the system date; injectable via `now` for tests/determinism) and refuse if
    it lags by more than `bundle_max_age_days`. `resolve_dates` internals are
    untouched — this is only an after-the-fact check.
  - `RecommendationConfig.bundle_max_age_days: int = 14` (calendar days; covers
    the longest A-share holiday so a normal pre-holiday gap does not false-fire,
    while a weeks/months-stale bundle does).
- `scripts/daily_recommend.py`: `--bundle-max-age-days` (default 14) — the
  escape hatch for an intentional historical run.

## Phase 2 ↔ Phase 3 coupling (intended, documented)

This guard is correct precisely because the current bundle IS stale: on a
machine whose system date is well past the bundle's last day (2025-12-30),
`recommend()` will now **refuse** to emit until the bundle is updated. That is
the desired behavior — the data really is stale — but it means the daily list
is blocked until the ops data-update chain (Phase 3: incremental tushare
re-fetch + full qlib bin rebuild) runs. The two phases are coupled by design:
the guard (Phase 2) makes automation safe; the data chain (Phase 3) keeps the
bundle fresh so the guard passes. For an intentional historical / back-dated
run, raise `--bundle-max-age-days`.

## Non-Goals

- Not the data-update chain itself (Phase 3) or scheduling (Phase 4).
- Not a trading-day-precise freshness check (would add a tushare `trade_cal`
  network dependency); the system-date + generous calendar-day tolerance is
  enough to catch the real "months stale" hazard without false-firing on
  holidays.
- Not the P1-1 hardcoded-path cleanup (separate ops PR).

## Impact

- **Affected spec**: `v2-daily-stock-recommendation` (ADDED — bundle freshness
  guard).
- **Affected code**: `src/inference/daily_recommend.py`,
  `scripts/daily_recommend.py`.
- **Affected tests**: `tests/logic/inference/test_daily_recommend.py`
  (`BundleFreshnessTests`).
- **Behavior change**: `recommend()` refuses on a stale bundle instead of
  silently emitting a stale list. **No test calls `recommend()` directly** (only
  the CLI), so no existing test false-reds; the guard logic is unit-tested via
  the pure helpers with an injected reference date.
- **Risk**: low and additive — the only behavior change is "stale → refuse"
  (the safe direction). The default 14-day tolerance is holiday-safe.
