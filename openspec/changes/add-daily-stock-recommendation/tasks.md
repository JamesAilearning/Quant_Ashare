## 1. Model artifact (Step 1)

- [x] 1.1 Check for an existing PIT-trained model artifact to reuse; if
  none, train one with the Phase A clean single-fold config (PIT,
  Alpha158, LGB GPU, embargo-safe train 2018→2023 / valid 2024) and
  save to an explicit path.
- [x] 1.2 Record model path, feature set (Alpha158), label definition
  (`Ref($close,-2)/Ref($close,-1)-1`), and the training/fit window in
  `docs/phase_b_result.md`.

## 2. Inference core ``src/inference/daily_recommend.py`` (Step 2)

- [x] 2.1 `RecommendationConfig` frozen dataclass (model path,
  provider_uri, instruments, as_of_date|None, topk, fit_start, fit_end,
  out_dir).
- [x] 2.2 `DailyRecommendationResult` frozen dataclass (as_of_date,
  picks list, n_scored, n_masked).
- [x] 2.3 `recommend(config)`: resolve as-of date (default = last PIT
  trading day) → load model → build as-of-T Alpha158 features
  (`end_time=T`, `fit_end_time=fit_end`) → `model.predict` → tradability
  mask (`compute_unavailable_mask(insts, T, T, pit_provider=...)`) →
  rank desc → Top-K → result.
- [x] 2.4 Best-effort name lookup from tushare `stock_basic` dump if on
  disk; empty + log note otherwise (no hard dependency).

## 3. CLI ``scripts/daily_recommend.py`` (Step 2)

- [x] 3.1 Arg parse (`--config` | inline flags, `--as-of`, `--topk`,
  `--out-dir`); call `recommend`.
- [x] 3.2 Write `daily_recommendation_<date>.csv` + `.json` (Top-K buy
  list) and a full scored-frame audit file; print the list.
- [x] 3.3 `if __name__ == "__main__"` + `multiprocessing.freeze_support()`
  (qlib joblib spawn guard — known Phase A trap).

## 4. Tests ``tests/logic/inference/test_daily_recommend.py`` (Step 3)

- [x] 4.1 **Look-ahead guard (red line)**: on a panel with data `> T`,
  assert as-of-T feature frame has max datetime `== T` (no `> T` rows).
- [x] 4.2 Tradability: a `T`-suspended / one-price-locked stock is
  excluded from the Top-K buy list.
- [x] 4.3 Ranking: output sorted by score desc, length ≤ topk, ranks
  contiguous 1..N.
- [x] 4.4 As-of resolution: default picks last calendar trading day;
  `--as-of` override on a historical day works.

## 5. Validation runs (Step 3)

- [x] 5.1 Run for a **historical** trading day (e.g. mid-2025); manually
  sanity-check picks were genuinely tradable that day; record in docs.
- [x] 5.2 Run for the **latest** PIT trading day; produce a real
  recommendation list; record a sample in docs.
- [x] 5.3 `pytest tests/logic/inference/ -q` green; document any坑.

## 6. Docs

- [x] 6.1 `docs/phase_b_result.md`: model source, script design,
  look-ahead-bias prevention proof, historical-day validation, latest-day
  sample list, known TODOs (ST / T+1 limit-up).
