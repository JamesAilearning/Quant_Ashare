# Tasks

- [x] Step 1 (data, operator-signed): ingest `H00906.CSI:SH000906TR` +
      `H00905.CSI:SH000905TR` into the canonical PIT bundle; coverage /
      integrity / plausibility numbers signed 2026-07-16.
- [x] `_CANONICAL_BENCHMARK_BY_UNIVERSE` in `backtest_runner.py`
      (csi300/all pinned to `SH000300TR`).
- [x] LOAD-time warning: universe-aware pairing via `universe_hint`
      (pipeline + walk-forward pass `config.instruments`); set-membership
      fallback for direct callers.
- [x] `config/presets/csi800.yaml`; fix `default.yaml` universe/benchmark
      mismatch.
- [x] Daily ingest DEFAULT map += both TR codes; orchestrated rebuild keeps
      `--best-effort ""` (all canonical indices mandatory).
- [x] Governance tests: mapping invariants / set membership / pairing
      (effective default included) / canonical-map ⊆ ingest-default /
      plan must not override `--index-map`.
- [x] Spec delta (this change) superseding the single-canonical sentence.
- [ ] Archive after merge (`/opsx:archive`).
