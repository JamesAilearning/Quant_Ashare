## 1. Publisher Implementation

- [x] 1.1 Add `src/data/benchmark_artifact_publisher.py` with `BenchmarkArtifactPublisher.publish(...)` and `BenchmarkArtifactPublisherError`.
- [x] 1.2 Publisher requires canonical qlib init via `is_canonical_qlib_initialized()`; raises on missing init.
- [x] 1.3 Publisher fetches `$close` for the supplied `benchmark_code` via `qlib.data.D.features`; raises if the result is empty.
- [x] 1.4 Publisher writes a csv with header `date,close` (dates as ISO strings, floats with stable precision).
- [x] 1.5 Publisher writes a sidecar manifest json with all required provenance fields; `snapshot_at` defaults to the caller-supplied `end_time`.
- [x] 1.6 Publisher delegates final profile construction to `BenchmarkArtifactLoader.load(...)` to guarantee producer/consumer symmetry.

## 2. End-to-End Logic Test

- [x] 2.1 Add `tests/logic/test_benchmark_publisher_e2e.py`.
- [x] 2.2 Test initializes canonical qlib runtime against `D:/qlib_data/my_cn_data` with region `cn`.
- [x] 2.3 Test skips gracefully if the local data bundle is not present (so CI on other machines stays green).
- [x] 2.4 Test writes artifacts to a temp directory, calls publish, feeds the returned profile into `BenchmarkDataContract`, asserts `contract_health == "ok"`.
- [x] 2.5 Test asserts the produced csv has at least one row and the manifest contains all required fields.
- [x] 2.6 Test asserts that calling publish without canonical init raises `BenchmarkArtifactPublisherError`.

## 3. Governance Regression Test

- [x] 3.1 Add `tests/governance/test_publisher_uses_canonical_init.py`.
- [x] 3.2 Test statically scans `src/data/` for `qlib.init(` and `from qlib import init`; asserts zero hits.
- [x] 3.3 Test statically asserts `src/data/benchmark_artifact_publisher.py` imports `is_canonical_qlib_initialized` from `src.core.qlib_runtime`.

## 4. Quality Gates

- [x] 4.1 Run the full unittest discovery. All tests pass.
- [x] 4.2 Confirm no existing spec, contract, or runtime semantics were altered.
