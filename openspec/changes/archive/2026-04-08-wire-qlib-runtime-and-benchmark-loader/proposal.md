## Why

All V2 data contracts are in place but no real data ever flows through them. The canonical backtest contract references its official path as the plain string `"qlib-native backtest_daily"`, with no compile-time anchor to a real qlib entry point. There is also no declared qlib dependency, no canonical qlib runtime initialization entry point, and no loader that can turn a real benchmark artifact into a `BenchmarkArtifactProfile`.

This creates three V1-style regression risks the governance baseline was explicitly meant to prevent:
1. **Competing official paths**: the canonical path is a string, so a future change could silently wire a different qlib function (e.g. `qlib.contrib.evaluate.backtest_daily`) without any import-level conflict.
2. **Hidden coupling in runtime init**: without a single `qlib.init` entry point, different subsystems may initialize qlib with different `provider_uri` / `region` / cache configs.
3. **Nominal-only contracts**: benchmark contract validation has never been exercised against real file IO, so schema drift and encoding problems cannot be caught by tests.

This change wires the first real data flow through the benchmark contract, pins qlib, and makes the canonical official path anchored to a real Python import.

## What Changes

- Declare qlib and numerical-stack dependency versions via `pyproject.toml` (qlib via local path).
- Add a single canonical qlib runtime initialization entry point (`src/core/qlib_runtime.py`) that forbids inconsistent re-initialization.
- Anchor `CanonicalBacktestContract` to the real qlib backtest callable via a Python import, so the official path is statically checkable.
- Add a benchmark artifact loader in `src/data/` that reads a csv + sidecar manifest and produces a `BenchmarkArtifactProfile` consumable by the existing benchmark data contract.
- Add fixture-based end-to-end logic tests for the benchmark loader → contract pipeline.
- Add governance regression tests for: single canonical backtest path, qlib init singleton, and no alternative backtest path leakage in `src/core/`.

## Capabilities

### New Capabilities
- `v2-qlib-runtime-bootstrap`: single canonical qlib runtime initialization entry point and anchoring of the canonical backtest official path to a real qlib import.
- `v2-benchmark-artifact-loader`: real file-IO loader that produces contract-consumable benchmark artifact profiles from csv + sidecar manifest.

### Modified Capabilities
- `v2-canonical-backtest-contract`: official path is now anchored to a concrete Python callable import, without changing the declared canonical semantics.

## Impact

- Affected code:
  - new: `pyproject.toml`, `src/core/qlib_runtime.py`, `src/data/benchmark_artifact_loader.py`
  - modified: `src/core/canonical_backtest_contract.py` (import anchor only)
  - new tests: `tests/logic/test_benchmark_loader_e2e.py`, `tests/governance/test_qlib_init_singleton.py`, `tests/governance/test_no_alt_backtest_path.py`, `tests/fixtures/benchmark/*`
- No runtime trading behavior is introduced. No canonical official-metrics definition is changed. Benchmark selection semantics remain out of scope.
- Existing contract-only tests must continue to pass unchanged.
