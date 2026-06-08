# Tasks: guard-corrupt-adj-factor-in-bin-builder

## 1. Implementation
- [x] Add `QlibBinBuilder._validate_adj_factor` (finite + strictly-positive
      check; error names ticker / trade_date / value).
- [x] Call it from `_apply_adjustment` after `ffill().fillna(1.0)`, before the
      OHLC multiply.

## 2. Tests
- [x] Direct unit tests: clean factors pass; `inf` / `0` / negative each raise
      and name the offending row; clean rows are NOT flagged.
- [x] Integration via `build()`: a corrupt `adj_factor` parquet aborts the build
      with `QlibBinBuilderError`; a clean factor still builds and adjusts.

## 3. Verification
- [x] `pytest tests/data_pipeline/test_qlib_bin_builder.py` green.
- [x] Full fast suite green (no regression); `ruff` + `mypy --strict` clean.
