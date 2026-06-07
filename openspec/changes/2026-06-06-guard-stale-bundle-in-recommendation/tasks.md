# Tasks: guard-stale-bundle-in-recommendation

## 1. Guard
- [x] `_bundle_is_stale(bundle_last_day, today, max_age_days)` — pure predicate
      (calendar-day lag > tolerance; bundle on/after today never stale).
- [x] `_assert_bundle_fresh(...)` — raises `DailyRecommendationError` with an
      actionable message (names the bundle's last day, the lag, and the remedy
      "update the bundle").
- [x] `recommend(config, *, now=None)`: after `resolve_dates`, compare
      `D.calendar()[-1]` vs `(now or date.today())`; `resolve_dates` internals
      untouched (only an after-the-fact check).
- [x] `RecommendationConfig.bundle_max_age_days: int = 14` (holiday-safe;
      style-aligned with `st_snapshot_max_age_days`).
- [x] `scripts/daily_recommend.py --bundle-max-age-days` (default 14) — the
      historical-run escape hatch.

## 2. Tests (BundleFreshnessTests)
- [x] is-stale predicate: gap < / == / > tolerance; bundle on/after today.
- [x] fresh bundle passes; holiday boundary (~9d, tol 14) does NOT false-fire.
- [x] stale bundle raises with an actionable message.
- [x] reference today is injectable (deterministic; not `datetime.now()`).

## 3. Scope / coupling (documented)
- [ ] Phase 2 ↔ Phase 3: the guard blocks the list until the bundle is updated
      (Phase 3 data chain). On the current stale bundle, `recommend()` now
      refuses — by design (proposal.md "Phase 2 ↔ Phase 3 coupling").

## 4. Quality gates
- [x] `ruff` + `mypy --strict` clean (`src/inference/daily_recommend.py`).
- [x] `pytest tests/logic/inference/test_daily_recommend.py` green (43 passed,
      2 RUN_E2E skipped); confirmed no test calls `recommend()` directly → no
      false-red from the new guard.
- [x] `openspec validate --strict`.
