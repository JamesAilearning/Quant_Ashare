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

The PIT-correct ST history needed for the **backtest** already exists
(`all_namechanges.parquet`, 8042 rows, 3502 with an ST marker), but wiring
it into the walk-forward changes the C1 baseline and needs a heavy RUN_E2E
re-generation. To keep this change small and baseline-neutral, this PR does
the **inference side only** (current ST, from the active-stocks snapshot the
path already loads); the backtest side is a separate PR (see Scope boundary).

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

- **Not** the backtest / walk-forward ST filter or the C1 baseline
  regeneration — that is PR2 (see Scope boundary).
- **Not** changing the universe-file construction (`all.txt` / csi*); ST is
  filtered at the prediction layer, consistent with how suspension is handled.
- **Not** changing the model, features, ranking, or topk semantics.
- **Not** point-in-time ST history; inference uses the current snapshot, which
  is correct for a "today" decision.

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

## Scope boundary & known intermediate inconsistency

This PR filters ST on the **inference (daily list)** side only, using the
**current** snapshot. It deliberately does **not** touch the backtest.

Until the follow-up PR (PR2 — backtest-side PIT historical-ST mask from
`all_namechanges` + a same-PR RUN_E2E C1-baseline regeneration) lands:

- the walk-forward backtest universe **still includes ST names**, so the
  committed C1 baseline (mean IR +0.301) reflects an *includes-ST* strategy;
- therefore the backtest does **not yet validate** the ST-excluded list this
  PR produces. The daily list excludes ST; the historical performance numbers
  do not yet reflect that exclusion.

This is a known, temporary inconsistency made explicit here so it is not a
silent gap. It is resolved by PR2.

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

**Name-only predicate has a structural blind spot — PR2 decision point.** The
zero-false-negative scan only covers names that *carry* an ST marker. It
structurally cannot see a current snapshot row whose name has *dropped* the
marker entirely (the `*金亚` class: name shows no `ST`, but `change_reason`
says `*ST`). `is_st_name` is name-only by design, so it returns `False` for
such a row and the stock would slip into the list. PR1 scopes this to
namechange-history (PR2 cross-checks `change_reason`). **PR2 must explicitly
decide whether to feed that `change_reason` cross-check into the
current/inference path too** — i.e. for each active stock, consult its latest
namechange `change_reason` to recover a "dropped-marker" current ST.
Otherwise the inference side stays name-only and this corner (the one blind
spot the reconciliation cannot detect) never closes. Incidence is low, but
it is a real PR2 decision, not a silent gap.

## Impact

- **Affected specs**: `v2-daily-stock-recommendation` (ADDED — two
  requirements: current-ST exclusion; fail-loud on missing/stale ST source).
- **Affected code**: `src/data/st_status.py` (new),
  `src/inference/daily_recommend.py`, `scripts/daily_recommend.py`.
- **Affected tests**: `tests/logic/test_st_status.py` (new),
  `tests/logic/inference/test_daily_recommend.py` (ST classes added).
- **Behaviour change**: the name source becomes REQUIRED for `recommend`
  (it was optional, for names only); a missing/stale source now fails loud.
  The buy list now excludes current ST names.
- **Risk**: low and inference-only; no backtest/baseline impact; the shared
  predicate is validated against the real tushare markers.
