## Context

The user approved changing recommendation timing, not models or execution policy. The old core reads entry-day OHLCV; the formal date rule selects the penultimate provider session and readers assume entry equals its tail. These boundaries must migrate together. Approved recovery data and fidelity evidence remain in the original main checkout; implementation is isolated in this worktree.

## Goals / Non-Goals

**Goals:** one canonical candidate generator, latest complete closed session by default, truthful unknown entry conditions, explicit source provenance, and strict reader migration.

**Non-Goals:** automatic orders, a new strategy, changes to ensemble/training/universe/Top50/N5, historical PIT-ST performance claims, extending qlib price calendars, modifying fetch manifests/providers, adding UI quarantine authorization, or waiving production backup/update/acceptance gates.

## Decisions

### Separate a schedule from recorded prices

Add a bounded manual `fetch_recommendation_calendar.py` command using the existing `TushareClient`. It writes a new, immutable-by-default JSON snapshot, not raw `trade_cal.parquet`. Its exact schema is `schema_version`, `source_api`, `exchange`, `query` (`start_date`, `end_date`, `fields`), `fetched_at`, and `rows` (`exchange`, `cal_date`, `is_open`). Dates use YYYYMMDD, rows cover every requested calendar day exactly, exchange is SSE, and markers are strict integers 0/1. Fetch is one real call; no retry loop or weekday approximation. A failed response leaves no output.

Reader validation and SHA-256 use the same byte buffer. An aware clock and fixed +08:00 timezone enforce a nonfuture snapshot no older than seven calendar days. Missing range days, duplicate days, wrong source/exchange, malformed dates/markers, or no planned next open session refuse. No online fetch occurs inside recommendation. Explicit CLI input or `QUANT_TRADING_CALENDAR`/documented deployment path selects the snapshot; absence never falls back to qlib's price tail.

`src.data.recommendation_calendar` provides `fetch_snapshot(client, *, start_date, end_date, now)`, `write_snapshot(path, snapshot)`, `read_snapshot(path, *, now) -> TradingCalendarSnapshot`, and `resolve_session_dates(price_calendar, snapshot, *, now, as_of_date=None) -> SessionDates`. `SessionDates` carries `as_of_date`, `entry_date`, `latest_closed_date`; the snapshot supplies a provenance projection containing path, SHA-256, source, query and acquisition timestamp.

### Closed-session and price evidence are distinct

Use an aware Shanghai clock and a conservative 15:30+08 cutoff to determine the latest scheduled closed session. This cutoff makes intraday data ineligible; it does NOT prove the vendor has supplied final bars. Default recommendation requires the provider tail to equal that closed session; a one-session lag refuses despite the existing 14-day coarse-age guard. Explicit historical `--as-of` must still be an actual price session no later than the latest closed session, and is a candidate recalculation using current ST evidence, never certified historical performance. Schedule and price sessions must agree throughout their overlapping span. A schedule must cover the clock date and the resolved following open day.

The first production fetch must actually begin after the selected T's cutoff and pass genuine completeness/fidelity gates; neither a wall-clock comparison nor a calendar snapshot replaces that operational proof. No clock injection is offered in the CLI. The existing Python `now` test seam becomes an aware datetime; migrate every caller/test rather than guessing a time from a date.

### One candidate path, no retrospective execution mode

Features, predictions and all market-data queries remain <=T. Reuse the canonical microstructure filter on T only. Ranking and quarantine precedence remain unchanged. `eligible_as_of` means eligible given T bars plus the current bound ST/quarantine evidence, not historical PIT eligibility or future tradability. Keep `n_masked` as the actual T-day known microstructure exclusions. Do not use empty future masks to mark stocks tradable. N5 is still evaluated on decision T using the existing price-calendar schedule functions, including honest nullable next-rebalance dates.

Alpha158 initializes all loader groups before `prepare(feature, DK_I)`. Its default forward-return label therefore must not be loaded during inference. Keep the unchanged 158 feature configuration and inference processors, but explicitly use an unused current-close label group (required by the existing grouped loader shape), with no learning processors. Never prepare that group, treat it as a return or change training/backtest labels. A synthetic regression executes the real Alpha158/DatasetH initialization and traps forward expressions at the raw loader call.

New picks and audit rows carry strict boolean `eligible_as_of` and `tradable_flag=null` for every row, including exclusions. The internal ranking/counting authority becomes `eligible_as_of`. New artifacts are schema 3 and require `recommendation_mode="next_session_candidate"`, `entry_tradability_status="unconfirmed"`; these values mirror into both CSVs, even header-only output. Keep all model/bundle identity fields and add required `meta.trading_calendar` and `meta.latest_closed_session`. Validate status, rows, finite scores, dates, counts, cadence and quarantine before creating any output file.

### Readers fail closed, candidates never become holdings

Use a small qlib-free shared timing/schema contract for writer and web readers, not a second calendar policy. V3 requires its explicit fields and null tradability; v2 and pre-v2 remain distinguishable historical read-only artifacts. Current-v3 status requires matching current model/provider identity and configured calendar path/SHA, as-of equal to provider tail and the generation's latest closed session, and an unexpired target entry session. Read only the bounded deployment-configured calendar file for that comparison, never an artifact-supplied path. The conservative 15:30+08 entry cutoff bounds current display; it does not verify execution. Invalid or expired candidates cannot be called current or silently reinterpreted as v2.

V3 candidates block nominal-holding baseline promotion and backtracking past them to an old v2 list. HOLD remains monitoring-only; a rebalance-day candidate still needs human trade-time checks. UI changes are only necessary schema/command/disclosure migration, no new page or authorization control.

## Risks / Trade-offs

- [Future suspension/limit status is unknowable] -> explicitly unconfirmed, human execution checks, no executable-list or live/backtest-equivalence claim.
- [Calendar changes or missing future publication] -> bounded snapshot freshness, exact source/range validation, refuse absent next open day.
- [Intraday price artifact could masquerade as closed] -> cutoff guard plus actual after-cutoff production fetch and independent data acceptance; no inferred completeness from the clock alone.
- [Old readers confuse booleans/null or stale entry dates] -> versioned schema, strict same-PR migration, synthetic negative tests and nominal-baseline blocking.

## Migration Plan

Run synthetic regression tests (including before-fix failure), full logic/governance suites, import checks, OpenSpec validation, local reviews, CI and a fresh Codex review before merging. Preserve the earlier successful fidelity receipts; adapt operational verification to the newly reviewed inference code without pretending old source pins identify it. Then acquire a real schedule snapshot, perform finite genuine candidate/refusal acceptance, take full same-point rollback backups, publish reversibly, run one actual production update and fixed-path candidate check, and restore the data scheduler last. Rollback restores the prior code/config/artifacts/task state, never relabeling candidates as executions.
