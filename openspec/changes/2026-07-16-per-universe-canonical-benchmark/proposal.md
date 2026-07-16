# Proposal: Per-universe canonical total-return benchmark (CSI800 expansion (b) Step 2)

## Why

The governing spec (`v2-ashare-survivorship-correction`) pins THE canonical
benchmark to the CSI 300 total-return index (`H00300.CSI`). That was correct
while csi300 was the only official universe. The CSI800 expansion breaks the
single-benchmark premise: a csi800 run measured against the csi300 basket's
return is a CATEGORY ERROR, not a comparison — its "excess return" mixes
genuine alpha with the csi300-vs-csi800 basket spread. The tracked
`default.yaml` preset was already living this mismatch (`instruments: csi800`
+ `benchmark_code: SH000300TR`).

Operator-approved decision chain (2026-07-16): CSI800 (b) route signed
(Step 1 TR ingest → Step 2 preset + per-universe benchmark governance →
Step 3 sleeve prep → Step 4 probe brief, each step individually gated);
Step 1 numbers signed (SH000906TR / SH000905TR: 2018-01-02..calendar tail,
2050 rows = full calendar, 0 NaN, 0 gap fills; cum 2018-01-02→2025-12-31:
300TR +36.9% / 906TR +35.9% / 905TR +33.1%).

## Honest state note (for the reviewer/signer)

The implementation rides PR #365 through the codex loop (this change file
included in the same PR): per-universe map + LOAD-time universe-aware
warning (r1), daily-ingest DEFAULT map extension so a fresh staging rebuild
cannot drop canonical codes (r2), this spec supersession (r3). The csi300 /
"all" entries stay pinned to `SH000300TR` — re-pointing them is a basis
change requiring its own REGEN, and the governance test hard-fails a mapping
edit that tries.

## What changes

- **MODIFIED** `v2-ashare-survivorship-correction` / "Benchmark indices
  SHALL be builder-adjacent staging products": the canonical benchmark
  becomes PER UNIVERSE (csi300/all → `H00300.CSI`=`SH000300TR` pinned,
  csi800 → `H00906.CSI`=`SH000906TR`, csi500 → `H00905.CSI`=`SH000905TR`),
  every canonical code rides the daily ingest DEFAULT map, the orchestrated
  rebuild treats every canonical index as MANDATORY (best-effort is a
  manual/standalone affordance only), and mis-paired canonical codes are
  surfaced at load time via the universe hint.
- NO change to ingest mechanics (staging-dir write, benchmark.txt registry,
  OHLC-from-close, ffill, tail truncation, no-$factor) — those requirements
  carry over verbatim.
- NO change to the REGEN-A price control (`000300.SH` = `SH000300`).

## Impact

- `src/core/backtest_runner.py`: `_CANONICAL_BENCHMARK_BY_UNIVERSE` +
  universe-aware LOAD warning (`universe_hint` threaded from pipeline /
  walk-forward call sites; canonical contract untouched).
- `scripts/data_pipeline/07_ingest_benchmark.py`: DEFAULT_INDEX_MAP += the
  two TR codes; manual best-effort default = all H*.CSI; orchestrated path
  unchanged (`--best-effort ""` = all mandatory).
- `config/presets/csi800.yaml` (new), `config/presets/default.yaml`
  (benchmark mismatch fixed to `SH000906TR` — the one behavior-visible
  change: default-preset excess basis aligns with its own universe).
- Governance: mapping invariants + set membership + universe↔benchmark
  pairing (effective-default included) + canonical-map ⊆ ingest-default.
