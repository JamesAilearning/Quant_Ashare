## Context

The V2 governance baseline requires exactly one canonical qlib-native official path and forbids hidden coupling in runtime initialization. Today the repository honors both rules only nominally: the canonical path is a string, qlib is not a declared dependency, and no loader ever exercises the benchmark contract against real files.

This change is the first runtime change after the contract-foundation phase. It deliberately keeps runtime trading semantics and strategy logic out of scope, and only wires the minimum needed to:
- anchor the canonical path to a real Python callable,
- lock qlib initialization to a single entry point,
- make the benchmark contract reachable from real file IO.

The local qlib source checkout lives at `D:/Qlib/qlib`. The local qlib data bundle lives at `D:/qlib_data/my_cn_data`.

## Goals / Non-Goals

**Goals:**
- Declare explicit, pinned dependency on qlib (from the local source path) and the numerical stack.
- Provide a single canonical entry point to initialize qlib.
- Replace the canonical-path string with a Python import anchor.
- Provide a real benchmark artifact loader that produces `BenchmarkArtifactProfile`.
- Exercise the benchmark contract against real fixture files end-to-end.
- Protect the new boundaries with governance regression tests.

**Non-Goals:**
- Do not implement any strategy, model, executor, or trading decision logic.
- Do not execute a real qlib backtest. `CanonicalBacktestContract.run_placeholder` remains `NotImplementedError`.
- Do not change any existing contract semantics, status categories, or validation rules.
- Do not add universe, taxonomy, or run-artifact loaders.
- Do not add UI.

## Decisions

1. **qlib dependency pinned via local path, not PyPI.**
   - Rationale: the user operates from a local source checkout. Pinning via `file:///D:/Qlib/qlib` keeps the dependency reproducible without depending on PyPI release cadence. `numpy<2.0` is enforced because qlib 0.9.x still assumes numpy 1.x dtype semantics.

2. **Single canonical qlib init entry point with re-init guard.**
   - Decision: `init_qlib_canonical(cfg)` stores the first config and raises if a second call passes a different config.
   - Rationale: V1 lesson "hidden coupling in app runtime initialization". Idempotent re-init with the same config is allowed for developer convenience.
   - Trade-off: tests need a narrow `_reset_canonical_qlib_runtime_for_tests()` escape hatch. It is placed in the same module with an explicit test-only name, and a governance test asserts it is only referenced from `tests/`.

3. **Canonical backtest path anchored to `qlib.backtest.backtest`.**
   - Decision: `CanonicalBacktestContract` imports the real callable and exposes `CANONICAL_OFFICIAL_BACKTEST_CALLABLE` plus a derived fully qualified path string. The legacy `qlib.contrib.evaluate.backtest_daily` is explicitly forbidden in `src/core/`.
   - Rationale: the "exactly one canonical official path" rule is now enforced at import time, not at documentation time.
   - Trade-off: `src/core/canonical_backtest_contract.py` now imports qlib unconditionally. Environments without qlib installed cannot import this module. This is acceptable because qlib is now a declared first-class dependency.

4. **Benchmark loader reads csv + sidecar manifest, does not touch qlib provider.**
   - Decision: the loader operates on the canonical artifact shape already defined by `BenchmarkDataContract`. It does not call `D.features` or `qlib.init`.
   - Rationale: this keeps the contract/runtime boundary clean. The loader's job is "file → profile", the contract's job is "profile → status". A separate future change can add a qlib-provider-backed artifact publisher.

5. **Fixture-based e2e tests instead of real provider reads.**
   - Rationale: test hermeticity. Real provider reads are for a separate change once the artifact publisher exists.

## Risks / Trade-offs

- [Risk] Importing qlib at module load in `canonical_backtest_contract.py` could slow down or break test collection on machines without qlib.
  - Mitigation: qlib is now a declared dependency; CI and dev environments must install it. Failure to install qlib is a correctly loud failure, not a silent fallback.
- [Risk] The `_reset_canonical_qlib_runtime_for_tests` escape hatch could be abused by non-test code.
  - Mitigation: governance regression test greps the repo for usages outside `tests/`.
- [Risk] Pinning numpy `<2.0` delays numpy 2.x adoption.
  - Mitigation: acceptable until qlib upstream supports it. Documented in `pyproject.toml` comment.

## Migration Plan

1. Land this change with `pyproject.toml`, qlib runtime entry point, canonical path anchor, benchmark loader, fixtures, and tests.
2. Next change: add a benchmark artifact publisher that reads from the qlib provider and writes the canonical csv + manifest shape.
3. Subsequent changes: repeat the "loader + publisher" pattern for universe, taxonomy, run-artifact contracts.
4. Only after the data flow is real end-to-end, open a separate change to implement `CanonicalBacktestContract.run` against `qlib.backtest.backtest`.

Rollback: revert the change. All contract-only tests are unchanged and will keep passing.

## Open Questions

- Should the qlib runtime config (provider_uri, region) be read from a repo-level config file or only from explicit caller input? Current design: caller-only. Deferred to a later operator-workflow change.
- Should the benchmark loader support parquet in addition to csv? Current design: csv only; parquet is a follow-up.
