# Tasks: exclude-st-from-recommendation

## 1. Shared ST predicate
- [x] `src/data/st_status.py` (new, pure): `is_st_name(name)` matching the ST
      family `^N?S?\*?ST(?![A-Za-z])` (ST/*ST/SST/S*ST/NST), excluding bare S,
      N/C, XD/XR/DR, PT, and Latin company names.
- [x] `current_st_codes(names_by_code)` — ST-flagged subset of a `{code: name}`
      map (code-format agnostic; reused by PR2's historical path).
- [x] Marker coverage driven by the real markers in `all_namechanges.parquet`
      / `active_stocks.parquet` (verified: matched *ST/ST/NST/S*ST/SST;
      excluded bare S, `*`-only truncated, N).
- [x] Case-sensitive markers; full-width ＊/Ｓ/Ｔ normalised to half-width
      (defensive — 0 in current data, verified in both files).
- [x] Zero-false-negative reconciliation on the current snapshot: the
      case-insensitive `"st"` ∪ half/full-star scan == `is_st_name` == 255.

## 2. Inference-side exclusion
- [x] `build_recommendation(..., st_excluded=frozenset())` — ST names
      non-tradable, dropped before the Top-K slice, labelled `"st"`;
      microstructure mask precedence over the ST label.
- [x] `recommend` builds the current-ST set from the already-loaded
      active-stocks snapshot (no second load path) and passes it through.
- [x] `DailyRecommendationResult.n_st_excluded`; JSON + CLI summary carry it.

## 3. Fail-loud on bad ST data
- [x] `_validate_st_snapshot` — raise if `name_source_parquet` is None /
      missing / stale (mtime lags as-of by > `st_snapshot_max_age_days`).
- [x] `_st_snapshot_is_stale(snapshot_date, as_of_date, max_age_days)` — pure
      staleness predicate; only OLD snapshots are stale (newer is left to PR2).
- [x] `RecommendationConfig.st_snapshot_max_age_days` (default 7).
- [x] Confirmed active_stocks has NO embedded snapshot/as-of column (only
      per-stock list_date), so staleness uses file mtime — documented as a
      weak proxy (sync/copy resets mtime) with the snapshot_date column as the
      near-term fix (§6).

## 4. Tests
- [x] `is_st_name`: ST/*ST/SST/S*ST/NST → True; plain, bare S, N, XD, DR, TCL,
      GQY, Latin STAR, truncated `*金亚`, empty/None → False.
- [x] `current_st_codes`: mixed map → ST-only subset; empty map → empty.
- [x] filter-then-take-K: pool with interspersed ST + topk=K → K highest
      NON-ST names (not K minus ST hits).
- [x] ST labelled `"st"` in audit frame; mask takes precedence when both.
- [x] `build_recommendation` without `st_excluded` is backward-compatible.
- [x] staleness predicate: within / at / beyond tolerance, newer snapshot.
- [x] fail-loud: None source raises; missing file raises; stale file raises;
      fresh file passes (mtime via `os.utime`).

## 5. Quality gates
- [x] `ruff check` clean on changed files.
- [x] `mypy --strict` clean on `src/data/st_status.py` +
      `src/inference/daily_recommend.py`.
- [x] `pytest tests/logic/test_st_status.py tests/logic/inference/` green
      (41 passed, 2 RUN_E2E skipped).

## 6. Scope boundary + near-term backlog (documented, NOT done here)
- [ ] PR2: backtest-side PIT historical-ST mask from `all_namechanges` +
      same-PR RUN_E2E C1-baseline regeneration. Until then the WF baseline
      reflects an includes-ST universe and does not validate the ST-excluded
      list (made explicit in proposal.md "Scope boundary").
- [ ] Near-term (not "someday"): write a `snapshot_date`/`as_of` column when
      fetching active_stocks (tushare `stock_basic`) and switch
      `_validate_st_snapshot` to read it instead of file mtime — the
      sync-proof staleness signal (proposal.md "Near-term backlog").
- [ ] PR2 decision point: name-only `is_st_name` cannot see a current row whose
      name DROPPED the ST marker (the `*金亚` class — the one blind spot the
      zero-false-negative scan can't detect). PR2 must decide whether to feed
      its `change_reason` cross-check into the inference path too; otherwise the
      inference side stays name-only and that corner never closes
      (proposal.md "Name-only predicate has a structural blind spot").
