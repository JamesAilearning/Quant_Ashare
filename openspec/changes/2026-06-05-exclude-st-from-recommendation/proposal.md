# Proposal: exclude-st-from-recommendation

## Why

A-share ST / *ST stocks (退市风险警示) are still trading but flagged for
delisting risk, carry a tighter ±5% daily price limit, and are not a
sensible target for a daily long pick. The daily recommendation path
(`src/inference/daily_recommend.py`) currently excludes only *untradable*
names (suspension / one-price-lock via the microstructure mask) — it does
**not** exclude ST names, so an ST/*ST stock that scores in the Top-K is
recommended for purchase. The fix is to drop current ST names from the
candidate pool before the Top-K slice.

The same exclusion must reach the **backtest**, or the historical performance
numbers describe an includes-ST strategy the live list no longer follows. This
capability is delivered in two PRs sharing one OpenSpec change: **PR1** the
inference side (current ST from the active-stocks snapshot) and **PR2** the
walk-forward side (PIT-historical ST from `all_namechanges`, as-of each
execution date) plus the same-PR RUN_E2E C1-baseline regeneration. Both use one
shared ST predicate so they cannot drift.

## Goals

- **Exclude current ST from the buy list.** `recommend` SHALL drop names
  whose current display name carries an ST-family marker from the candidate
  pool **before** the Top-K slice, so the list holds K tradable, non-ST
  picks (not K minus the ST hits).
- **One shared ST predicate.** A pure `is_st_name(name)` in
  `src/data/st_status.py` is the single definition of "counts as ST", reused
  by PR2's historical path (fed namechange names) so the two never drift.
- **Auditable, not silent.** Excluded ST names stay in the full scored frame
  with `unavailable_reason = "st"`; the result/JSON carry `n_st_excluded`.
- **Fail-loud on bad data.** A missing or stale current-ST source SHALL raise
  rather than emit a possibly-unfiltered list.

## Non-Goals

- **Not** changing the universe-file construction (`all.txt` / csi*); ST is
  filtered at the prediction/selection layer, consistent with how suspension
  is handled.
- **Not** changing the model, features, ranking, or topk semantics. The
  backtest ST mask is **selection-only** — the model still trains on the full
  panel (ST included).
- **Not** ST-masking the single-fold `Pipeline.run` (`config.yaml`) backtest.
  `config.yaml` sets no `namechange_path`, so its (unconditional) backtest step
  runs **ST-UNMASKED** (logged as a WARN). That single-fold backtest is a
  training sanity check, NOT an ST-strategy-faithful metric — only the
  walk-forward (`config_walk.yaml`) excludes ST. The daily list (inference) is
  ST-excluded regardless. (To make the single-fold faithful too, set
  `namechange_path` in `config.yaml`; deliberately left off to keep PR2 scoped
  to the WF baseline.)
- **Not** using `change_reason` for ST detection (decided name-only — see
  "Name-only blind spot" below).

## What Changes

1. `src/data/st_status.py` (new, pure — no qlib/IO):
   - `is_st_name(name)` — matches the ST family `^N?S?\*?ST(?![A-Za-z])`:
     `ST`, `*ST`, `SST`, `S*ST`, and resumption-day `NST`; excludes bare `S`
     (share-reform), `N`/`C` (new listing), `XD`/`XR`/`DR` (ex-div/rights),
     `PT` (pre-2007, absent here), and Latin company names (`TCL`, `STAR…`).
     Coverage driven by the markers actually present in the tushare data.
     Case-sensitive (uppercase markers; a lowercase "st" would false-positive
     on Latin names). Full-width marker glyphs ＊/Ｓ/Ｔ are normalised to
     half-width first — defensive against a classic tushare trap (0 in the
     current data, verified, but future-proofs the shared predicate).
     Verified zero false negatives on the current snapshot: the case-
     insensitive `"st"` ∪ half/full-star scan flags exactly the 255 names
     `is_st_name` flags.
   - `current_st_codes(names_by_code)` — the ST-flagged subset of a
     `{code: name}` map (code-format agnostic).
2. `src/inference/daily_recommend.py`:
   - `recommend` builds the current-ST set from the already-loaded
     active-stocks snapshot and passes it to `build_recommendation`.
   - `build_recommendation(..., st_excluded=frozenset())` — ST names are
     non-tradable, excluded before Top-K, labelled `"st"`; microstructure
     masking takes precedence over the ST label when a name is both.
   - `_validate_st_snapshot` / `_st_snapshot_is_stale` — fail-loud if the
     source is absent or its file mtime lags the as-of date by more than
     `RecommendationConfig.st_snapshot_max_age_days` (default 7). active_stocks
     has NO embedded snapshot-date column (verified — only per-stock
     `list_date`), so mtime is the only available signal. mtime is a WEAK
     proxy: a sync/copy that rewrites mtime to "now" makes a stale file look
     fresh and lets the guard pass — see the near-term backlog below.
   - `DailyRecommendationResult.n_st_excluded` + `n_st_excluded` in the JSON.
3. `scripts/daily_recommend.py`: print `st_excluded=` in the run summary.
4. Tests: `tests/logic/test_st_status.py` (predicate + set);
   `tests/logic/inference/test_daily_recommend.py` (filter-then-take-K, reason
   label, mask precedence, staleness predicate, fail-loud missing/stale).

### Backtest side (PR2)

5. `src/data/st_history.py` (new, pure): PIT historical-ST reconstruction as an
   **as-of `start_date` step function** (`name_on` / `is_st_on`): the name in
   effect on date D is the row with the greatest `start_date <= D`. `end_date`
   is **ignored** (51% null + heavy interval overlap in the real data —
   unreliable); `start_date` is 0% null. **Full-row dedup** (the table is ~40%
   exact duplicates) — NOT key-subset, so same-`(ts,start)` different-name rows
   are kept and a period is ST if **any** name that day is ST. Defaults to
   **non-ST** before an instrument's first record / for an absent instrument.
   `change_reason` is **deliberately not used** (see the resolved decision
   below). Reuses PR1's `is_st_name`. `compute_st_mask(pairs, lookup)` returns
   the `(date, instrument)` drop-set + an attribution list.
6. `src/data/pit/_common.py`: `qlib_to_ts_code` (inverse of `to_qlib_ticker`),
   extracted from `daily_recommend._qlib_code_to_ts_code` so the inference ST
   filter and the backtest ST mask share one conversion (no behaviour change to
   the inference path — same logic, now shared + tested).
7. `src/core/backtest_runner.py`: a **parallel ST mask** at the existing
   `apply_mask_to_predictions` seam (after the microstructure mask, before
   `TopkDropoutStrategy`), evaluated on the **execution date** of the shifted
   predictions. New optional `namechange_path` / `st_audit_path` kwargs:
   `namechange_path=None` → ST mask disabled with a WARN (backward compatible);
   set → fail-loud on missing/unreadable/malformed/uncovered namechange, drop
   ST `(date, instrument)` rows, WARN with counts, and write the attribution
   CSV to `st_audit_path`. **Selection-only**: the model trains on the full
   panel (ST included) upstream; only the buy-list selection is masked.
8. `namechange_path` config field on `WalkForwardConfig` + `PipelineConfig`
   (default `None`); threaded at both `BacktestRunner.run` call sites
   (`pipeline.py`, `walk_forward/engine.py`); enabled in `config_walk.yaml`
   (the canonical WF config the C1 baseline extends).
9. Tests: `tests/logic/test_st_history.py` (as-of/boundary/dedup/ambiguity/
   default/mask-seam/fail-loud); `tests/data_pipeline/test_common.py`
   (`qlib_to_ts_code`).

## Scope — both sides done; intermediate inconsistency RESOLVED

Both sides now exclude ST: inference (current snapshot) and backtest
(PIT-historical, as-of). The PR1-era intermediate inconsistency (the WF
baseline reflecting an includes-ST universe) is **resolved by this PR**: the
canonical `config_walk.yaml` enables the namechange ST mask, so a regenerated
C1 baseline reflects the ST-excluded universe.

**Operator action — BEFORE merge, on the PR2 branch (not auto-run by the
agent):** regenerate the C1 baseline under `RUN_E2E=1`
(`python scripts/generate_regression_baseline.py
tests/regression/fixtures/walk_forward_baseline_config.yaml`), eyeball the
headline drift vs the +0.301 reference (csi300 has very little ST → expect a
**small** drift; a large IR swing is a red flag for over-exclusion / a
look-ahead bug — check the per-fold `fold_NN_st_mask_audit.csv` to confirm the
drop is a small, named set, e.g. 乐视退, not a broad sweep), then commit the
regenerated `walk_forward_baseline_metrics.json` **onto the PR2 branch before
merging**. This must close inside PR2: the drift test is E2E-gated, so CI
cannot catch the mismatch — merging code-that-excludes-ST with a
baseline-that-includes-ST would leave `main` in an inconsistent state (a wrong
baseline + a window where the policy and the fixture disagree). The whole point
of the same-PR policy is that a CI-invisible inconsistency must be closed by
process, on the branch, before merge.

## PIT coverage limitation (documented)

`assert_covers` guards the **end** of the window (the namechange snapshot must
reach the eval end — the recency/staleness concern). The **start** is covered
by reconstruction semantics, not an extra check: before an instrument's first
namechange record, `is_st_on` defaults to **non-ST**. This is correct because a
stock's pre-first-record name is its original/IPO name, and a stock is never ST
at IPO (ST is a post-listing designation). The namechange table reaches back to
2010-06 (global min `start_date`), well before the 2018+ backtest window, so it
is not truncated at the start.

The residual blind spot is **per-stock completeness**: 776 of 1555 ts_codes
have their earliest record after 2018, and ~366 of those are ST-ever. For the
typical case the earliest record is the stock's *first* ST transition (before
it, the non-ST IPO name — the default is correct). But if tushare's history is
missing an *earlier* ST record for a stock (e.g. an earliest record that is
already `*ST`, implying a prior plain-`ST` period not in the table), the
window-early ST is silently treated as non-ST. Incidence on csi300 is low; the
mitigations are (a) tushare namechange being the canonical complete source,
(b) the per-fold `st_mask_audit.csv` letting the operator spot an anomalous
drop pattern during the baseline regen, and (c) the manual-override escape
hatch. A per-stock start-coverage check (cross-referencing `list_date`) is
deferred — a table-level check cannot catch this and would be theater.

## Near-term backlog (NOT done here — must be scheduled, not "someday")

**Embedded snapshot-date for the ST source.** `active_stocks.parquet` has no
snapshot/as-of column, so staleness falls back to file mtime. In an
environment whose sync/copy tool rewrites mtime to "now", a stale snapshot
reads as fresh and the staleness guard passes silently — the exact leak the
guard exists to prevent. The fix is to write a `snapshot_date` (or `as_of`)
column when fetching active_stocks (tushare `stock_basic`) and switch
`_validate_st_snapshot` to read that column instead of mtime. This is small
and lives in the data-fetch layer; it is the correct, sync-proof staleness
signal and should be scheduled near-term, not deferred indefinitely.

**Name-only blind spot — DECIDED (PR2): name-only, no `change_reason` rescue.**
PR1 flagged the truncated-name corner (`*金亚`: name shows no `ST` but
`change_reason` says `*ST`) for a possible PR2 `change_reason` cross-check.
PR2's read of the real `all_namechanges` data **closes that loop in favour of
name-only**: `change_reason` disagrees with the name in ~795 rows (788 ST-name
/ non-became-ST-reason e.g. 摘帽; 7 became-ST-reason / non-ST-name, one of which
— 001267 汇绿生态 — has no marker at all and a reason-rescue would *wrongly*
exclude it), so using it would trade a tiny false-negative for a worse
false-positive. The blind spot itself is ~zero impact: of the 7 truncated rows,
only 乐视退 touches csi300 and it is delisted (handled by the delisting layer).
The escape hatch, if a specific gap is ever found, is a curated manual-override
file (mirroring `data/manual_delistings.yaml`) — NOT an automatic
`change_reason` rule. This applies to **both** the inference and backtest paths
(both name-only), so the inference-side follow-up loop is closed too.

## Impact

- **Affected specs**: `v2-daily-stock-recommendation` (ADDED — inference:
  current-ST exclusion + fail-loud on missing/stale/malformed ST source;
  backtest: PIT-historical ST exclusion at selection before TopkDropout).
- **Affected code**: PR1 — `src/data/st_status.py` (new),
  `src/inference/daily_recommend.py`, `scripts/daily_recommend.py`. PR2 —
  `src/data/st_history.py` (new), `src/data/pit/_common.py` (`qlib_to_ts_code`),
  `src/core/backtest_runner.py`, `src/core/pipeline.py`,
  `src/core/walk_forward/{config,engine}.py`, `config_walk.yaml`.
- **Affected tests**: `tests/logic/test_st_status.py`,
  `tests/logic/test_st_history.py`, `tests/data_pipeline/test_common.py` (new),
  `tests/logic/inference/test_daily_recommend.py` (ST classes).
- **Behaviour change**: the buy list excludes current ST; the WF backtest
  (via `config_walk.yaml`) excludes PIT-historical ST → the C1 baseline must be
  regenerated (operator RUN_E2E, this PR). `BacktestRunner.run` gains optional
  `namechange_path`/`st_audit_path` (default None → ST mask off + WARN, so
  existing callers are unaffected). The inference name source is REQUIRED.
- **Risk**: backtest masking is selection-only (training untouched) and
  default-off (no caller breaks); the shared predicate is validated against the
  real tushare markers; PIT reconstruction uses only `start_date` (no
  look-ahead) and fails loud on missing/stale/uncovered data.
