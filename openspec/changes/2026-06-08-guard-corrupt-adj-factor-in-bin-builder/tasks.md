# Tasks: guard-corrupt-adj-factor-in-bin-builder

## 1. Implementation
- [x] Add `QlibBinBuilder._validate_adj_factor` (finite incl. not-`NaN` +
      strictly-positive check; error names ticker / trade_date / value).
- [x] Call it from `_apply_adjustment` on the RAW adj source BEFORE the
      merge / `ffill().fillna(1.0)`, so a present-row `NaN` is caught (a date
      absent from the source still fills to `1.0`).

## 2. Tests
- [x] Direct unit tests: clean factors pass; `inf` / `NaN` / `0` / negative each
      raise and name the offending row; clean rows are NOT flagged.
- [x] Integration via `build()`: a corrupt `adj_factor` parquet aborts the build
      with `QlibBinBuilderError`; a clean factor still builds and adjusts.

## 3. Verification
- [x] `pytest tests/data_pipeline/test_qlib_bin_builder.py` green.
- [x] Full fast suite green (no regression); `ruff` + `mypy --strict` clean.
