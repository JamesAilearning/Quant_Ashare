# Add Factor Mining Foundations — Operators, Expression Tree, Grammar

## Why

Phase 0 produced `docs/factor_mining/{inventory,decisions,scale_invariance,factor_mining_claude_code_design,factor_mining_phase1_preflight}.md`
(commit `c56545f`). Those documents lock the contract for the
factor-mining subsystem but commit no code. Phase 1 is the first
phase that materialises that contract as Python.

Phase 1 builds the **foundational primitives** of the factor-mining
subsystem:

- the **operator library** (CPU reference implementations),
- the **expression tree** (typed AST, serialisation, structural hash),
- the **grammar** (type system + random generator) including the
  two-tier scale-invariance taint check formalised in
  `docs/factor_mining/scale_invariance.md`.

It performs **no data access**. It does not import qlib, does not
touch `PITDataProvider`, does not compute IC, does not write a GP
loop, does not touch GPU. Per `factor_mining_claude_code_design.md`
§0.1 and §6, the project's phases are sequenced as one OpenSpec change
per phase, with each phase strictly scoped. Phase 2 (PIT evaluator),
Phase 3 (GP engine), Phase 4 (GPU), Phase 5 (handler registration),
and Phase 6 (validation + promotion) are explicitly out of scope here.

This Phase-1 layer is what every later phase imports. If its types,
hashes, or scale-invariance gate are wrong, every downstream phase
inherits the bug. The proposal therefore prioritises **correctness
proofs over capability surface**: the §5 pinned examples from
`scale_invariance.md` and the 1000-random-expression generator test
are the load-bearing acceptance gates.

### Why this needs a spec, not just code

Three constraints are non-derivable from `src/`:

1. **The two-tier scale-invariance type system** (`scale_invariance.md`)
   is normative, not stylistic. Cross-sectional operators MUST reject
   `ADJ_TAINTED` inputs because the qlib bins store pre-adjusted prices
   with an as-of-today `adj_factor` snapshot — a factor whose ranking
   depends on that constant has high in-sample IC and silent live
   failure (`inventory.md` §F.2, `scale_invariance.md` §1).
2. **The feature universe is exactly the six PIT bin fields** per `decisions.md` D3,
   finalised after Phase 0 confirmed the bin schema. `$vwap` is a
   derived expression (`$money / $volume`), not a terminal; `$turn` is
   deferred to v2.
3. **The strict data gate** (`decisions.md` D5) — zero matches of
   `qlib.data`, `qlib.init`, or `from qlib` anywhere under
   `src/factor_mining/`. This rule survives drift better than a "one
   exception" rule and is enforced from Phase 1 onwards.

A code-only delivery cannot encode these constraints durably. They
need to live as `SHALL` requirements that future phases inherit.

### Module-location decision (Phase 0 outcome O1)

`factor_mining_claude_code_design.md` §3.1 places factor mining under
**`src/factor_mining/`** so the operator/expression/grammar code is
importable from production training paths via the
`v2-feature-handler-registry` registration seam (Phase 5). This
diverges from the existing `v2-project-skeleton-boundaries` spec,
which was written when factor research code was expected to live under
`research/factor_lab/`. Per `decisions.md` Phase 0 outcome O1, this
proposal MODIFIES `v2-project-skeleton-boundaries` to acknowledge
`src/factor_mining/` as a production-layer subpackage. The
`research/factor_lab/` placeholder is unaffected — it remains
research-only by contract.

## What Changes

- **Add new capability `v2-factor-mining-foundations`** with the
  Phase 1 contract: module placement, no-data-access gate, operator
  catalogue (28 operators, ts_cov excluded per `scale_invariance.md`
  §4), numerical-stability invariants, PIT-gap respect via
  `min_periods=window`, commutative-hash equality, typed AST with
  kind × taint, feature universe (six PIT bin fields), the
  scale-invariance gate (root = `(CSF, PURE)`; `cs_*` rejects
  `ADJ_TAINTED`; eight pinned pass/fail examples from
  `scale_invariance.md` §5), and the random-generator contract
  (1000 samples, 100% type-valid AND scale-pure, `min_depth=2`).
- **MODIFY `v2-project-skeleton-boundaries`** to acknowledge
  `src/factor_mining/` as a production-layer subpackage governed by
  `v2-feature-handler-registry` and `v2-factor-mining-foundations`,
  distinct from the `research/factor_lab/` placeholder. The
  pre-existing requirement "Research factor_lab SHALL remain
  non-production by contract" is preserved unchanged.
- **Add `src/factor_mining/{__init__,operators,expression,grammar}.py`**
  with CPU-only reference implementations. No data, no qlib, no GPU.
- **Add `tests/logic/factor_mining/`** with:
  - `test_operators.py` — per-operator edge-case matrix (NaN, zero,
    negative, constant, empty, single-row, PIT-gap input).
  - `test_expression.py` — serialisation round-trip, structural
    hash stability, commutative-hash equality.
  - `test_grammar.py` — 1000-random-expression generator test
    (100% type-valid AND scale-pure; `min_depth=2`).
  - `test_scale_invariance.py` — eight pinned pass/fail examples from
    `scale_invariance.md` §5, plus the additional rejected forms
    enumerated below the table.
  - `test_integration_smoke.py` — hand-built 20-day reversal
    `cs_rank(div_safe(ts_delta($close, 20), $close))` parses,
    type-checks, round-trips, pretty-prints, hashes stably.
- **Cite `scale_invariance.md` as a normative reference** in the
  Phase 1 spec (not merely informational).

## Non-Goals

- **No data access whatsoever.** No `qlib.init`, no `qlib.data.D`, no
  `from qlib import …`, no `PITDataProvider` construction. Phase 2
  owns all of that. The strict grep gate (`decisions.md` D5) enforces
  this from Phase 1 onwards.
- **No IC, RankIC, IR, or fitness computation.** Deferred to Phase 2.
- **No GP engine, mutation, crossover, tournament selection, or
  population.** Deferred to Phase 3.
- **No GPU kernels or CuPy.** Deferred to Phase 4, and only if Phases
  1–3 are green and CPU is the bottleneck.
- **No `MinedFactorHandler`, no `FeatureDatasetBuilder` integration,
  no `MinedFactor` registry entry.** Deferred to Phase 5.
- **No validator, no promotion CLI, no `research/mined_factors/`
  directory.** Deferred to Phase 6.
- **No edits to `src/pit/`, `src/data/pit/`, `src/core/`,
  `src/data/feature_dataset_builder.py`, or any existing pipeline
  code.** Phase 1 is purely additive in `src/factor_mining/` and
  `tests/logic/factor_mining/`.
- **No `ts_cov` operator.** Excluded by `scale_invariance.md` §4 —
  `cov(a×x, y) = a × cov(x, y)` re-introduces `adj_factor` taint, and
  `ts_corr` (which is taint-invariant) already covers the v1 use
  case. May be reconsidered in v2 if a `ts_cov_safe` variant is
  designed.
- **No `group_by` parameter sampling.** `decisions.md` D2 keeps the
  hook in the operator signature but the Phase 1 grammar samples only
  `group_by=None`. No industry / size table is consulted.
- **No `$turn` terminal.** Not in PIT bins per `decisions.md` D3;
  deferred to v2 pending a separate Tushare `daily_basic` ingest.
- **No performance tuning.** Phase 1 optimises for correctness; the
  preflight's "5s for 5000 stocks × 250 days" target is a sanity
  bound, not a benchmark.
