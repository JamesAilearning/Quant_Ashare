## 1. Publisher Implementation

- [ ] 1.1 Add `src/data/benchmark_artifact_publisher.py` with `BenchmarkArtifactPublisher.publish(...)` and `BenchmarkArtifactPublisherError`.
- [ ] 1.2 Publisher requires canonical qlib init via `is_canonical_qlib_initialized()`; raises on missing init.
- [ ] 1.3 Publisher fetches `$close` for the supplied `benchmark_code` via `qlib.data.D.features`; raises if the result is empty.
- [ ] 1.4 Publisher writes a csv with header `date,close` (dates as ISO strings, floats with stable precision).
- [ ] 1.5 Publisher writes a sidecar manifest json with all required provenance fields; `snapshot_at` defaults to the caller-supplied `end_time`.
- [ ] 1.6 Publisher delegates final profile construction to `BenchmarkArtifactLoader.load(...)` to guarantee producer/consumer symmetry.

## 2. End-to-End Logic Test

- [ ] 2.1 Add `tests/logic/test_benchmark_publisher_e2e.py`.
- [ ] 2.2 Test initializes canonical qlib runtime against `D:/qlib_data/my_cn_data` with region `cn`.
- [ ] 2.3 Test skips gracefully if the local data bundle is not present (so CI on other machines stays green).
- [ ] 2.4 Test writes artifacts to a temp directory, calls publish, feeds the returned profile into `BenchmarkDataContract`, asserts `contract_health == "ok"`.
- [ ] 2.5 Test asserts the produced csv has at least one row and the manifest contains all required fields.
- [ ] 2.6 Test asserts that calling publish without canonical init raises `BenchmarkArtifactPublisherError`.

## 3. Governance Regression Test

- [ ] 3.1 Add `tests/governance/test_publisher_uses_canonical_init.py`.
- [ ] 3.2 Test statically scans `src/data/` for `qlib.init(` and `from qlib import init`; asserts zero hits.
- [ ] 3.3 Test statically asserts `src/data/benchmark_artifact_publisher.py` imports `is_canonical_qlib_initialized` from `src.core.qlib_runtime`.

## 4. Quality Gates

- [ ] 4.1 Run the full unittest discovery. All tests pass.
- [ ] 4.2 Confirm no existing spec, contract, or runtime semantics were altered.
