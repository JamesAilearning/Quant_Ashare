## Why

The previous change wired the benchmark data contract to a real loader that reads a csv + sidecar manifest. The loader is the "consumer" side of the benchmark artifact boundary, but the "producer" side is still missing. Today the only way to get a benchmark artifact into the canonical shape is to hand-write a csv, which defeats the point of having a declared qlib provider at `D:/qlib_data/my_cn_data`.

Without a publisher:
- The benchmark contract can be validated, but only against fixture files.
- There is no reproducible way to refresh benchmark artifacts from the pinned qlib data bundle.
- `source_name` / `source_uri` / `snapshot_at` metadata has no single canonical provenance writer, so the provenance fields that the contract requires can drift per author.

This change adds a publisher that turns "pinned qlib provider + explicit benchmark_code + explicit date range" into the exact csv + manifest shape the loader already consumes. The round-trip (publisher → loader → contract) closes the first full benchmark data flow.

## What Changes

- Add `src/data/benchmark_artifact_publisher.py` implementing `BenchmarkArtifactPublisher.publish(...)`.
- Publisher reads close series from the qlib provider through `qlib.data.D.features`, writes csv and manifest json with canonical-shape columns and required provenance metadata, and returns the `BenchmarkArtifactProfile` that `BenchmarkDataContract.validate_and_build_status` expects.
- Publisher refuses to call `qlib.init` itself; it requires that canonical qlib runtime init has already happened via `src.core.qlib_runtime.init_qlib_canonical`, and raises a typed `BenchmarkArtifactPublisherError` otherwise.
- Add `tests/logic/test_benchmark_publisher_e2e.py` that initializes qlib through the canonical entry point against the local data bundle and verifies the full publisher → loader → contract round trip yields `contract_health == "ok"`.
- Add `tests/governance/test_publisher_uses_canonical_init.py` that statically scans `src/data/` to confirm no publisher module calls `qlib.init` directly.

## Capabilities

### New Capabilities
- `v2-benchmark-artifact-publisher`: canonical-shape benchmark artifact producer backed by the pinned qlib provider. Enforces canonical qlib init boundary and shared provenance metadata.

### Modified Capabilities
- None. The benchmark data contract and loader are consumed unchanged.

## Impact

- New files:
  - `src/data/benchmark_artifact_publisher.py`
  - `tests/logic/test_benchmark_publisher_e2e.py`
  - `tests/governance/test_publisher_uses_canonical_init.py`
- No existing runtime, contract, or loader semantics are changed.
- The e2e test touches the local qlib data bundle at `D:/qlib_data/my_cn_data` and uses a real, stable stock code (`SH600000`) as the "publishable benchmark-shaped instrument" because the user's data bundle does not contain the SH000300 index directly. This choice is intentional and documented in the test: the benchmark contract's `benchmark_code` is a free-form label, not an index identifier.
- No trading semantics, strategy logic, or backtest execution is introduced. Runtime trading behavior remains out of scope.
