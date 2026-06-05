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
- [x] CLI exposes `--name-source` + `--st-max-age-days` (Codex P2 on #222:
      the now-required name source must be overridable for non-default
      layouts, like `--provider-uri` / `--delisted-registry`).

## 3. Fail-loud on bad ST data
- [x] `_validate_st_snapshot` — raise if `name_source_parquet` is None /
      missing / stale (mtime lags as-of by > `st_snapshot_max_age_days`) /
      malformed (unreadable, missing `ts_code`/`name` column, or empty —
      Codex P1 on #222: a fresh-but-malformed snapshot would let
      `_load_name_map` return {} and silently disable ST filtering).
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
      malformed schema (missing `name`) raises; empty snapshot raises; fresh
      valid file passes (mtime via `os.utime`).

## 5. Quality gates
- [x] `ruff check` clean on changed files.
- [x] `mypy --strict` clean on `src/data/st_status.py` +
      `src/inference/daily_recommend.py`.
- [x] `pytest tests/logic/test_st_status.py tests/logic/inference/` green
      (41 passed, 2 RUN_E2E skipped).
- [x] PR2: full `tests/logic tests/governance tests/data_pipeline` green
      (2290 passed, 25 RUN_E2E skipped); `ruff` + `mypy --strict` clean on the
      7 changed src files; `openspec validate --strict` passes.

## 6. Backtest-side exclusion (PR2)
- [x] `src/data/st_history.py` (new, pure): as-of `start_date` step function
      (`name_on` / `is_st_on`), `end_date` ignored, full-row dedup (not
      key-subset), same-day any-ST, default non-ST before first record; reuses
      `is_st_name`; `compute_st_mask(pairs, lookup)` -> drop-set + attribution.
- [x] `src/data/pit/_common.py`: `qlib_to_ts_code` extracted from
      `daily_recommend` (shared, same logic, no inference regression).
- [x] `backtest_runner.py`: parallel ST mask at the `apply_mask_to_predictions`
      seam (after microstructure, before TopkDropout), on the execution date;
      `namechange_path`/`st_audit_path` kwargs (None -> disabled + WARN);
      selection-only (training panel untouched); attribution CSV written.
- [x] `namechange_path` on `WalkForwardConfig` + `PipelineConfig` (default
      None); threaded at both call sites; enabled in `config_walk.yaml`.
- [x] fail-loud: `load_namechange` (missing/unreadable/missing-col/empty) +
      `assert_covers` (latest record before eval end) raise `StHistoryError`
      -> `BacktestRunnerError` (no ST-unmasked fallback).
- [x] Provenance (Codex P2 on #223): `_build_provenance` folds the ST inputs
      (namechange path + content sha256 + masked count) into the fingerprint,
      so ST off-vs-on and a changed namechange snapshot move the fingerprint.

## 7. Backtest tests (PR2)
- [x] `test_st_history.py`: start_date-inclusive boundary; as-of step
      (became→摘帽); future start no look-ahead; default non-ST before first /
      absent ts; end_date ignored; full-row dedup collapses; same-`(ts,start)`
      different-name NOT deduped + any-ST; same-day all-non-ST stays non-ST;
      `compute_st_mask` pairs+attribution + mask-seam via
      `apply_mask_to_predictions`; fail-loud (missing/unreadable/missing-col/
      empty/build-missing-col) + `assert_covers` stale/ok.
- [x] `test_common.py`: `qlib_to_ts_code` exchanges + ts-passthrough +
      round-trip with `to_qlib_ticker` (no inference regression).
- [x] `test_backtest_runner.py::ProvenanceFingerprintTests`: ST off-vs-on and
      different namechange content each change the fingerprint; st_mask block
      surfaced in `config` (Codex P2).
- [x] `test_baseline_st_provenance_consistency.py` (non-E2E forcing guard,
      Codex P2 "or hide" on #223): FAILS when the resolved config enables ST
      but the committed baseline fixture's `_provenance.config_keys` lacks
      `namechange_path` — CI-enforces the on-branch regen (red until done),
      closing the small-drift hiding gap. Skips if the fixture is absent.

## 8. Operator action + remaining backlog
- [ ] Operator (this PR, RUN_E2E): regenerate the C1 baseline
      (`scripts/generate_regression_baseline.py
      tests/regression/fixtures/walk_forward_baseline_config.yaml`), eyeball the
      small expected drift vs +0.301 (large swing = red flag; check
      `fold_NN_st_mask_audit.csv` for a small named drop set), commit the new
      `walk_forward_baseline_metrics.json` in this PR.
- [ ] Near-term (not "someday"): `snapshot_date`/`as_of` column at fetch time
      for BOTH active_stocks (inference staleness) and namechange, replacing the
      mtime / latest-record-date proxies (proposal.md "Near-term backlog").
- [x] DECIDED: name-only, no `change_reason` rescue (data shows ~795
      name/reason disagreements; blind spot ~0 on csi300). Closes the PR1
      inference-side `change_reason` follow-up loop for both paths; manual
      override is the escape hatch if a specific gap is found.
