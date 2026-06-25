# Daily-update trading-calendar gate (non-trading-day no-op)

## Why

阶段5 scheduling: a scheduled (PR-P) daily run will invoke `run_daily_update` every
calendar day. On a non-trading day there is no new bar to ingest, so running the full
fetch → build → atomic-swap pipeline is wasted work that also churns the bundle (a new
`<provider>.new` staging + swap) for no data change, and a scheduler reading a
slow/again-rebuilt run every weekend is noise. The orchestrator should no-op cleanly on
closed days.

This adds a **trading-calendar gate** to `run_daily_update`: on a default run whose date
is not a trading day, exit 0 (success) without running any fetch/build/swap stage. The
gate is placed AFTER the dry-run preview (a `--dry-run` still prints the plan) and AFTER
the Stage 0 startup crash-repair — so an interrupted prior swap (live provider missing,
`.bak`/`.new` present) is ALWAYS completed even on a closed day, never left broken over
the weekend. An explicit `--end-date` (a deliberate backfill / catch-up) bypasses the
gate and runs.

### Scope of "trading day" (deliberate, with rationale)

The determination is a WEEKEND check (Sat/Sun) — **offline + deterministic**, so the
orchestrator hot path and the test suite take NO network (the "no real fetch in dev" red
line; there is no offline source for whether a FUTURE date is an exchange holiday). This
covers ~90% of non-trading days. A-share **weekday holidays** (~10/yr) are intentionally
NOT skipped by this gate: they fall through to the normal run, whose fetch/freshness
gates already no-op gracefully on a day with no new bar (the PR #270/#271 holiday-aware
floor), so a weekday holiday is handled correctly, never WRONGLY skipped. Full
holiday-awareness via the SSE exchange calendar (tushare `trade_cal`) is a documented
follow-up — it would add a network call to this gate and is better wired by the PR-P
scheduler entry than baked into the orchestrator default.

This does NOT change any existing stage behavior; it only adds an early clean exit on
weekends.
