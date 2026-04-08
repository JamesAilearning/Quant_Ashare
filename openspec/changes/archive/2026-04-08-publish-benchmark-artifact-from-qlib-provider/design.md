## Context

The benchmark data contract, loader, and canonical qlib runtime entry point are already in place. The only piece missing to close the first real data flow is a publisher that writes canonical-shape benchmark artifacts from the qlib provider.

This change is deliberately narrow:
- It only introduces a producer for benchmark artifacts.
- It does not touch the universe, taxonomy, or run-artifact contracts.
- It does not add any strategy, model, executor, or trading semantics.
- It does not alter the canonical backtest contract or its anchor to `qlib.backtest.backtest`.

## Goals / Non-Goals

**Goals:**
- Provide a single canonical producer for benchmark artifacts in the shape the loader already consumes.
- Force canonical qlib init to happen through `src.core.qlib_runtime.init_qlib_canonical` before any publisher call.
- Guarantee publisher output round-trips through the existing loader and contract without any additional transformation.
- Surface empty / partial qlib results as explicit errors, not silent empty files.

**Non-Goals:**
- Do not add a scheduler, cron, or workflow for refreshing artifacts.
- Do not add parquet output.
- Do not add a universe or taxonomy publisher (each gets its own scoped change later).
- Do not add retries, network IO, or qlib provider health checks.
- Do not implement benchmark selection semantics; `benchmark_code` remains a caller-supplied label.

## Decisions

1. **Publisher is a thin adapter over `qlib.data.D.features`.**
   - Decision: request `$close` for the single supplied `benchmark_code` over the supplied date range and flatten the MultiIndex `(instrument, datetime)` DataFrame into `date,close` rows.
   - Rationale: this matches the canonical csv shape the loader already validates. Any additional columns would expand the contract surface and should land as a separate spec change.

2. **Canonical qlib init is a precondition, not a side effect.**
   - Decision: the publisher calls `is_canonical_qlib_initialized()` at entry and raises `BenchmarkArtifactPublisherError` if init has not already happened. It NEVER calls `qlib.init` itself.
   - Rationale: V1 lesson "hidden coupling in app runtime initialization". Giving the publisher permission to init would create a second canonical init path, and the `src/core/qlib_runtime.py` singleton rule would be immediately violated.
   - Trade-off: callers must explicitly init canonical runtime before calling publish. A governance regression test statically scans `src/data/` for `qlib.init` calls to lock this in.

3. **Empty qlib result is an error, not a warning.**
   - Decision: if `D.features` returns an empty DataFrame for the supplied inputs, the publisher raises `BenchmarkArtifactPublisherError` with the inputs echoed back.
   - Rationale: publishing an empty artifact is exactly the kind of "silent bad output" V1 was punished for. An empty file would look contract-healthy on disk and the contract only catches it as `missing_artifact_file` if the file does not exist at all.

4. **Publisher returns the same `BenchmarkArtifactProfile` the loader would produce.**
   - Decision: after writing csv + manifest to disk, the publisher delegates to `BenchmarkArtifactLoader.load(...)` to read its own output back and return the resulting profile.
   - Rationale: guarantees producer/consumer symmetry — there is exactly one way to shape a `BenchmarkArtifactProfile`, and it is the loader's. This removes the risk of publisher and loader drifting.

5. **`snapshot_at` metadata defaults to `end_time`, not "now".**
   - Decision: if the caller does not override `snapshot_at`, the publisher uses the supplied `end_time` (caller-provided, deterministic) rather than `datetime.today()`.
   - Rationale: reproducibility. "now" makes two identical runs produce different manifests and defeats `config_fingerprint` semantics that the run-artifact contract will eventually depend on.

## Risks / Trade-offs

- [Risk] The local data bundle at `D:/qlib_data/my_cn_data` does not include the SH000300 index itself.
  - Mitigation: tests use `SH600000` as a publishable stable stock code. The benchmark contract accepts any caller-supplied `benchmark_code` string; this does not violate contract semantics.
- [Risk] `D.features` MultiIndex shape could change across qlib versions.
  - Mitigation: qlib is pinned via `docs/qlib-pin.md` and the publisher implementation uses a conservative, version-robust flattening path (reset_index, column rename).
- [Risk] Tests that actually init qlib leave global state behind.
  - Mitigation: `_reset_canonical_qlib_runtime_for_tests` is called in `tearDown`. qlib itself does not expose a clean tear-down, but re-init with the same config is idempotent by design, and the test runner does not require a fresh qlib state between test methods.

## Migration Plan

1. Land this change and confirm the full publisher → loader → contract round trip is green against the local data bundle.
2. Next change: `publish-universe-artifact-from-qlib-provider` — same pattern for the universe contract.
3. Later: repeat for taxonomy and run-artifact.

Rollback: revert the change. No existing contract semantics are touched; the loader still works against hand-written fixtures.

## Open Questions

- Should the publisher accept a list of benchmark codes and emit one csv per code, or one row per code per date? Current design: single code only. Multi-code is a follow-up.
- Should `source_uri` record the qlib provider path or the final on-disk csv path? Current design: `source_uri` records the qlib provider URI (`qlib-provider://<provider_uri>/<benchmark_code>`), because that is where the canonical truth lives. `artifact_path` already records the on-disk csv.
