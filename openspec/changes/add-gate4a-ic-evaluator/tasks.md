# Tasks: Gate-4A per-candidate IC evaluator (C1_GPA 先行)

## OpenSpec (propose stage)

- [x] Decision ledger recorded in proposal (方案 A / C1 先行 / ②收益几何 /
      ③total_mv + 两 pin,操作人批准 2026-07-14/15)。
- [x] `openspec validate add-gate4a-ic-evaluator --strict` green.
- [x] Operator signed (2026-07-15): DP1 (staleness cap 20 td) / DP2
      (JSON single-write + docs/research summary PR) / DP3 (full-span
      missing total_mv = hard fail; per-stamp missing = drop + count).

## Implementation (already delivered on PR #354, codex r7 CLEAN — merge
## pending; items map 1:1 to the spec requirements)

- [x] `scripts/research/gate4a_ic_evaluator.py`: gate-ACCEPT precondition
      (subprocess, output archived), fold geometry from frozen chain,
      canonical fold_phase stamp mirroring (fillable rule, zero-horizon
      drop+count), primary-only registered aggregate + tail diagnostics,
      fold-contained lag-1 forward returns (three counted outcomes),
      four-layer counted universe filtering (membership − financial −
      ST(exec) − microstructure(exec)), total_mv as-of deciles with
      20-td staleness cap, frozen data-root binding (no CLI override),
      PITDataProvider routing (post-delist mask; C["kernels"]=1),
      cross-endpoint report-period alignment, non-finite/sliver/count
      fail-louds, result.json + report.md + gate_accept.txt artifacts.
- [x] `src/research/financial_pit_view.py`: opt-in
      `include_report_periods` metadata (default output byte-identical).
- [x] `tests/logic/test_gate4a_ic_evaluator.py` (24) +
      `tests/logic/test_financial_pit_view.py` battery +1 (24) green;
      ruff + mypy --strict green.
- [x] `docs/prereg/quality_profitability_ledger.yaml`: E011 pre-run
      registration (C1 4A dev run; ignition condition = merge + gate
      ACCEPT).

## Follow-up once DP3 is signed

- [x] Full-span missing-total_mv hard-fail assertion (+ unit test):
      PR #355 (membership-overlap semantics, pre-span delistings exempt).

## Post-merge (run stage, per E011)

- [ ] Gate ACCEPT on clean main with the ACTUAL invocation (codex #356
      r1 P2 — `--store-dir` is required):
      `python scripts/research/gate3_prereg_gate.py --candidate C1_GPA
      --store-dir D:/qlib_data/financial_pit_raw
      --run-config config/presets/quality_gate3_dev_c1_gpa.yaml`
- [ ] Fire the C1 4A dev run (single process, nothing concurrent);
      archive artifacts.
- [ ] Ledger E012 post-run entry (results verbatim) + results-doc PR
      under docs/research/.
