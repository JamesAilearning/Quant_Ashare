## 1. Dependency Declaration

- [x] 1.1 Add `pyproject.toml` declaring qlib (from local path `D:/Qlib/qlib`), numpy `<2.0`, pandas `<2.3`, pyarrow, and dev extras (pytest, ruff, mypy).
- [x] 1.2 Document the pinned qlib commit in `docs/qlib-pin.md`.

## 2. Canonical qlib Runtime Entry Point

- [x] 2.1 Add `src/core/qlib_runtime.py` with `QlibRuntimeConfig`, `init_qlib_canonical`, `get_canonical_qlib_config`, and a test-only reset helper.
- [x] 2.2 Guarantee re-init with the same config is idempotent; re-init with a different config raises `QlibRuntimeInitError`.

## 3. Canonical Backtest Path Anchoring

- [x] 3.1 Import `qlib.backtest.backtest` inside `src/core/canonical_backtest_contract.py` and expose `CANONICAL_OFFICIAL_BACKTEST_CALLABLE`.
- [x] 3.2 Derive `CANONICAL_OFFICIAL_BACKTEST_PATH` from the real callable's module and qualified name.
- [x] 3.3 Keep the rest of `CanonicalBacktestContract` unchanged, including the `NotImplementedError` in `run_placeholder`.

## 4. Benchmark Artifact Loader

- [x] 4.1 Add `src/data/benchmark_artifact_loader.py` exposing `BenchmarkArtifactLoader.load(artifact_path, manifest_path, reference_date=None)`.
- [x] 4.2 Loader reads csv with header `date,close`, tolerates whitespace, rejects NaN in `close`, computes `rows`, `snapshot_start`, `snapshot_end`, `stale_days`, and a conservative `coverage_ratio` based on calendar-day span.
- [x] 4.3 Loader reads sidecar manifest json into `metadata`; records whether artifact and manifest files are present; propagates any temporal anomalies (`has_future_data`).
- [x] 4.4 Loader never raises on data-level problems; those are surfaced via the contract status. Loader DOES raise on malformed arguments (missing path strings).

## 5. Fixtures and End-to-End Tests

- [x] 5.1 Add `tests/fixtures/benchmark/SH000300.csv` and `tests/fixtures/benchmark/SH000300.csv.manifest.json` representing a valid healthy snapshot.
- [x] 5.2 Add `tests/logic/test_benchmark_loader_e2e.py` covering: healthy snapshot → `ok`; missing manifest → `error`; NaN in close → `error` via schema mismatch; stale snapshot → `warning`; future-dated snapshot → `error`.
- [x] 5.3 Tests run under the project's existing unittest discovery and must not depend on any network or qlib provider.

## 6. Governance Regression Tests

- [x] 6.1 Add `tests/governance/test_qlib_init_singleton.py` asserting: idempotent re-init with same config succeeds; re-init with different config raises `QlibRuntimeInitError`; reset helper only restores test state.
- [x] 6.2 Add `tests/governance/test_no_alt_backtest_path.py` asserting: `CANONICAL_OFFICIAL_BACKTEST_CALLABLE` is `qlib.backtest.backtest`; `CANONICAL_OFFICIAL_BACKTEST_PATH == "qlib.backtest.backtest"`; no file under `src/core/` references `qlib.contrib.evaluate.backtest_daily`.
- [x] 6.3 Extend (or add alongside) the existing canonical backtest contract test to assert the new `CANONICAL_OFFICIAL_BACKTEST_CALLABLE` constant is callable and points at the expected module.

## 7. Proposal Quality Gates

- [x] 7.1 Run `python -m unittest discover -v` from repo root. All tests pass.
- [x] 7.2 Run `openspec validate wire-qlib-runtime-and-benchmark-loader --strict` (if `openspec` CLI is available).
- [x] 7.3 Confirm no existing contract semantics were changed and no new runtime trading behavior was introduced.
