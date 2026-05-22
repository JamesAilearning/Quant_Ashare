# Design: Factor Mining Foundations (Phase 1)

> The long-form design (architecture, module layout, phase
> sequencing, success criteria) lives at
> `docs/factor_mining/factor_mining_claude_code_design.md`. The
> normative scale-invariance type rules live at
> `docs/factor_mining/scale_invariance.md`. The Phase 0 inventory
> findings live at `docs/factor_mining/inventory.md`. The
> contract-level decisions surfaced into OpenSpec scope are below.

## Phase 0 Outcomes That Drive This Phase

Three Phase 0 findings (`decisions.md` §"Phase 0 outcomes") shape the
Phase 1 spec:

- **O1 — Module location**: `src/factor_mining/` is a new
  production-layer module, NOT `research/factor_lab/`. Drives the
  MODIFY against `v2-project-skeleton-boundaries`.
- **O2 — Adjusted-price contract is stronger than the original
  design assumed**: the qlib bins store pre-adjusted prices with an
  as-of-today `adj_factor` snapshot, so any expression whose value
  depends on a per-ticker `adj_factor` constant is NOT PIT-correct
  cross-sectionally. Drives the two-tier `kind × taint` type system
  formalised in `scale_invariance.md`.
- **O3 — Field-set is 6, not 8** per `decisions.md` D3: PIT bins
  contain `$open $high $low $close $volume $money`. `$vwap` is a
  derived expression (`$money / $volume`); `$turn` is deferred.

## Module Layout (Phase 1 only)

```
src/factor_mining/
├── __init__.py            # public re-exports; no side effects
├── operators.py           # CPU operator library (28 operators)
├── expression.py          # Expression AST, serialisation, structural hash
└── grammar.py             # ExprType, OperatorRegistry, random generator

tests/logic/factor_mining/
├── test_operators.py      # per-operator edge matrix incl. PIT-gap
├── test_expression.py     # round-trip + hash stability + commutativity
├── test_grammar.py        # 1000-random-expression generator test
├── test_scale_invariance.py  # 8 pinned pass/fail from §5 of scale_invariance.md
└── test_integration_smoke.py # 20-day reversal hand-build
```

No `pit_adapter.py`, `evaluator.py`, `fitness.py`, `factor_pool.py`,
`gp_engine.py`, `miner.py`, `gpu_compute.py`, `validator.py`,
`promote.py`, or `handlers/mined_factor_handler.py` — all deferred to
later phases per `factor_mining_claude_code_design.md` §3.1.

## Operator Catalogue (v1)

Source of truth: `scale_invariance.md` §4. The catalogue below
enumerates the operators and matches the §4 taint propagation table.
Counts: 4 arithmetic + 5 unary + 4 cross-sectional + 14 time-series
+ 1 conditional = **28 operators**.

| Family | Operators |
|--------|-----------|
| Arithmetic (`T_FLOAT × T_FLOAT → T_FLOAT`) | `add`, `sub`, `mul`, `div_safe` |
| Unary (`T_FLOAT → T_FLOAT`) | `neg`, `abs`, `sign`, `log_safe`, `sqrt_safe` |
| Time-series (`T_FLOAT × T_INT_WINDOW → T_FLOAT`) | `ts_mean`, `ts_std`, `ts_max`, `ts_min`, `ts_sum`, `ts_delta`, `ts_pctchange`, `ts_rank`, `ts_argmax`, `ts_argmin`, `ts_skew`, `ts_kurt`, `ts_decay_linear`, and `ts_corr` (`T_FLOAT × T_FLOAT × T_INT_WINDOW → T_FLOAT`) |
| Cross-sectional (`T_FLOAT → T_CSF`) | `cs_rank`, `cs_zscore`, `cs_demean`, `cs_winsorize` |
| Conditional (`T_FLOAT × T_FLOAT × T_FLOAT → T_FLOAT`) | `where` |

**Excluded from v1**: `ts_cov` (`scale_invariance.md` §4 — `cov(a·x, y)
= a · cov(x, y)` re-introduces `adj_factor` taint; redundant with
`ts_corr` for v1 use). `group_by` parameter on `cs_*` operators —
hook preserved in operator signature per `decisions.md` D2, but the
random generator samples only `group_by=None`.

## Numerical-Stability Invariants

Per `factor_mining_phase1_preflight.md` §4.1 and design doc §5.2. The
operator implementations defensively handle the cases below; the
`test_operators.py` matrix asserts each cell.

| Operator | Behaviour |
|----------|-----------|
| `div_safe` | zero / near-zero denominator → NaN, never ±Inf |
| `log_safe`, `sqrt_safe` | input ≤ 0 → NaN (no raise) |
| `ts_rank` | constant window → 0.5 (mid rank), not NaN |
| `cs_zscore` | per-day std ≈ 0 → 0 (not NaN — avoids contagion across the cross-section) |
| `ts_corr` | ±Inf → NaN; constant window → NaN |
| `ts_std`, `ts_skew`, `ts_kurt` | NaN when window lacks sufficient non-NaN samples |
| **all `ts_*`** | `min_periods = window` (no partial-window leaks across PIT gaps) |

### The PIT-gap test (new in v2)

Every `ts_*` operator's unit test feeds a series with an explicit NaN
hole and asserts the operator **does not bridge** the hole. This is
the unit-level guarantee that mined factors will not cross entity
boundaries when Phase 2 wires PIT data in. The hole is constructed as
e.g. `[1.0, 2.0, NaN, NaN, NaN, 6.0, 7.0, 8.0]` with `window=3`; the
operator's output across the hole and for `window-1` positions after
the hole MUST be NaN.

## Expression Tree

The AST is a small set of `frozen` dataclasses:

- `Terminal` — leaf node carrying a feature name (`$open`, `$high`,
  `$low`, `$close`, `$volume`, `$money`) or a constant
  (`IntWindow(n=5|10|20|40|60)`, `Scalar(0.5|1.0|2.0)`).
- `OperatorCall` — internal node carrying an operator name and a
  tuple of child `Expression` nodes.

Three derived properties:

- **`output_type: ExprType`** — computed at `__post_init__` by
  consulting the operator registry. Construction of an ill-typed
  expression raises `GrammarError` (per `scale_invariance.md` §6.3 —
  no "build now, validate later" mode).
- **Serialisation** — `to_dict()` / `from_dict()` round-trip cleanly;
  `to_qlib_string()` produces a human-readable form suitable for
  later qlib-expression conversion (e.g. `cs_rank(div_safe(ts_delta($close, 20), $close))`
  pretty-prints as `CSRank((Delta($close, 20) / $close))`).
- **Structural hash** — `__hash__` is computed bottom-up; commutative
  operators (`add`, `mul`, plus `min`, `max` if symmetric binary
  versions are added later) **sort their child hashes before
  hashing**, so `add($close, $volume)` and `add($volume, $close)`
  hash identically (per `factor_mining_phase1_preflight.md` §4.2).
  Without this, GP wastes 30–50% of its search budget on
  permutation-equivalent duplicates.

## Type System (kind × taint)

Source of truth: `scale_invariance.md` §3. Two orthogonal axes:

```python
OutputKind = Literal["FEATURE", "FLOAT", "INT_WINDOW", "SCALAR", "CSF"]
ScaleTaint = Literal["PURE", "ADJ_TAINTED"]

@dataclass(frozen=True)
class ExprType:
    kind: OutputKind
    taint: ScaleTaint = "PURE"
```

### Terminal types

| Terminal | `kind` | `taint` |
|----------|--------|---------|
| `$open`, `$high`, `$low`, `$close` | `FEATURE` | `ADJ_TAINTED` |
| `$volume`, `$money` | `FEATURE` | `PURE` |
| `IntWindow(5|10|20|40|60)` | `INT_WINDOW` | `PURE` |
| `Scalar(0.5|1.0|2.0)` | `SCALAR` | `PURE` |

`$vwap` is NOT a terminal. The expression
`cs_rank(div_safe($money, $volume))` covers the VWAP-rank intent and
matches `scale_invariance.md` §5 example 6.

### Operator taint propagation

Full table at `scale_invariance.md` §4. Phase 1's `OperatorRegistry`
encodes each rule and exposes `Operator.output_type(*input_types)`
returning a fully resolved `ExprType` or raising `GrammarError` on
illegal combinations. Notable rules:

- `add`, `sub`: `(PURE, ADJ) → ILLEGAL` (units error — mixing
  scale-free and adj-tainted is rejected at type-check time, not
  silently coerced).
- `div_safe`: `(ADJ, ADJ) → PURE` — same-ticker ratio cancels
  `adj_factor`. **This is the only way price-derived expressions
  become scale-free.**
- `mul`: `(ADJ, ADJ) → ADJ` (quadratic `adj²` pollution; not useful
  but legal — the cs_* gate catches it at root).
- `log_safe`, `sqrt_safe`: preserve taint — `log(a·x) = log(a) + log(x)`
  still varies per ticker.
- All `ts_*` ratio/rank/correlation operators (`ts_pctchange`,
  `ts_rank`, `ts_argmax`, `ts_argmin`, `ts_corr`, `ts_skew`,
  `ts_kurt`): **always `PURE`** regardless of input.
- All `ts_*` linear operators (`ts_mean`, `ts_std`, `ts_max`,
  `ts_min`, `ts_sum`, `ts_delta`, `ts_decay_linear`): **preserve
  input taint**.
- `cs_*` operators: require `input.taint == PURE` else
  `GrammarError`; output is `(CSF, PURE)`.
- `where(cond, a, b)`: `cond.taint == PURE`; `a.taint == b.taint`;
  output taint = `a.taint`.

### Root rule

Every Phase 1 expression's root MUST satisfy
`output_type.kind == "CSF" AND output_type.taint == "PURE"`.

Both are implied by the cs_* gate at root (any `cs_*` returns
`(CSF, PURE)`, and `cs_*` only accepts `PURE` inputs), but the
grammar asserts both at the top level as defence-in-depth against
operators added later.

## Eight Pinned Examples (`scale_invariance.md` §5)

`test_scale_invariance.py` pins these eight expressions verbatim,
asserting the type-checker's verdict matches the table:

| # | Expression | Verdict |
|---|------------|---------|
| 1 | `cs_rank(ts_pctchange($close, 20))` | ✅ PASS |
| 2 | `cs_rank($close)` | ❌ FAIL |
| 3 | `cs_rank(ts_delta($close, 20))` | ❌ FAIL |
| 4 | `cs_rank(div_safe(ts_delta($close, 20), $close))` | ✅ PASS |
| 5 | `cs_zscore($volume)` | ✅ PASS |
| 6 | `cs_rank(div_safe($money, $volume))` | ✅ PASS |
| 7 | `cs_rank(log_safe($close))` | ❌ FAIL |
| 8 | `cs_rank(log_safe(div_safe($close, Ref($close, 20))))` | ✅ PASS |

The additionally-enumerated rejected forms from `scale_invariance.md`
§5 — `cs_rank(add($close, $open))`, `cs_rank(mul($close, $volume))`,
`cs_rank(ts_mean($close, 20))` — are also pinned. Adding a new
operator without updating this table is treated as a code smell per
`scale_invariance.md` §6.4.

## Random Expression Generator

`grammar.random_expression(target_type, max_depth, min_depth=2, rng)`
takes a **target `ExprType`**, not just an `OutputKind`. When
generating under a `cs_*` operator the recursive call carries
`taint=PURE` as a constraint, and the leaf/operator picker filters by
that constraint (per `scale_invariance.md` §6.2).

Acceptance gate: 1000 samples generated with `max_depth=6,
min_depth=2` MUST be 100% type-valid AND have every root with
`output_type == ExprType("CSF", "PURE")`. The test runs with a fixed
RNG seed for reproducibility; failure flags the generator's taint
constraint as broken.

The `min_depth=2` choice prevents trivial expressions like
`cs_rank($close)` (which would also fail the taint gate) and
`cs_rank($volume)` (which is type-valid but uninteresting — the
factor is just rank-of-volume).

## Strict Data-Access Gate (D5)

`decisions.md` D5 mandates zero matches of `qlib.data`, `qlib.init`,
or `from qlib` anywhere under `src/factor_mining/`. The Phase 1
proposal asserts this for the files it ships
(`__init__`, `operators`, `expression`, `grammar`). The spec
requirement is stated as a hard invariant; the grep is run as part of
the validation step in `tasks.md` (and is a candidate for the
pre-commit hook in Phase 2 per the `decisions.md` D5 action item).

For Phase 1, the requirement is straightforward to satisfy: pure
Python arithmetic + pandas/numpy, no qlib imports anywhere. A failing
grep at Phase 1 means a scope leak (e.g. someone added an
`evaluator.py`); the proper response is to revert the leak, not to
relax the grep.

## Integration Smoke Test (Hand-Built 20-Day Reversal)

`test_integration_smoke.py` exercises the Phase 1 surface end-to-end
**without any data**:

```python
expr_str = "cs_rank(div_safe(ts_delta($close, 20), $close))"
expr = parse_expression(expr_str)

assert expr.output_type == ExprType("CSF", "PURE")

serialized = expr.to_dict()
expr2 = Expression.from_dict(serialized)
assert hash(expr) == hash(expr2)

assert expr.to_qlib_string() == "CSRank((Delta($close, 20) / $close))"
```

This is the same expression used in `scale_invariance.md` §5
example 4 — it's the canonical "PIT-correct 20-day reversal" factor.
Phase 2 will evaluate it against real PIT data and compare its IC to
the contaminated baseline; Phase 1 only proves the expression's
machinery (parse → type-check → round-trip → hash → pretty-print).

## Out-of-Scope (Phase 1)

- **Data access of any form.** No `PITDataProvider`, no qlib, no
  pandas read of bin files.
- **IC, IR, RankIC, turnover, fitness.** Phase 2.
- **GP engine, mutation, crossover, tournament, population, niche
  penalty.** Phase 3.
- **GPU operators, CuPy, batched eval, memory management.** Phase 4.
- **`MinedFactorHandler` registration, `FeatureDatasetBuilder`
  wiring, pipeline integration.** Phase 5.
- **IS/OOS validator, promotion CLI,
  `research/mined_factors/{runs,candidates,production}/`.** Phase 6.
- **`ts_cov` operator.** `scale_invariance.md` §4 — taint propagation
  rules it out for v1.
- **`group_by` parameter sampling.** Hook present per `decisions.md`
  D2; random generator samples only `group_by=None`.
- **Industry / size neutralisation.** `decisions.md` D2 — no
  PIT-aware industry source exists; deferred to v2.
- **`$turn` terminal.** Not in PIT bins; `decisions.md` D3.
- **Performance tuning.** Phase 1 is correctness; the
  preflight sanity bound (5 s for 5000 stocks × 250 days on
  `ts_corr`) is informational only.

## Module-Boundary MODIFY Against `v2-project-skeleton-boundaries`

`decisions.md` Phase 0 outcome O1 mandates that this proposal MODIFY
`v2-project-skeleton-boundaries` to acknowledge `src/factor_mining/`
as a production-layer subpackage governed by
`v2-feature-handler-registry` (registration seam, used in Phase 5)
and `v2-factor-mining-foundations` (this capability).

The MODIFY is **additive in spirit**: the existing requirement
"Research factor_lab SHALL remain non-production by contract" is
**preserved unchanged**. `research/factor_lab/` continues to be a
research-only placeholder. The only change is that the directory
skeleton requirement enumerates `src/factor_mining/` alongside
`src/core/`, `src/data/`, etc., with a scenario clarifying its
production-layer status and the no-import-from-research rule.

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Type system holes — generator produces an expression that "should" be invalid but happens to type-check | 1000-random-expression test must be 100% type-valid AND scale-pure; 8 §5 pinned tests assert known-failing cases actually fail |
| Hash inconsistency from dict ordering / traversal order | Explicit commutativity test in `test_expression.py`; structural-equality test on canonicalised hashes |
| `ts_*` bridges PIT gap silently | Per-operator PIT-gap unit test; `min_periods=window` mandatory |
| Phase 1 silently widens to call `D.features` "just to validate the operator works" | D5 grep gate run in `tasks.md` validation; no `qlib` import allowed in `src/factor_mining/` |
| Operator added later forgets to declare `output_type` rule | `OperatorRegistry` registration validates that every registered operator has an `output_type` function before the registry is sealed |
| The grammar accepts `cs_rank(group_by="industry")` even though no industry source exists | `group_by` parameter retained per `decisions.md` D2 hook, but the random generator samples only `group_by=None` AND the `cs_*` constructor rejects any non-`None` value at type-check time in v1 |
| Scope creep — agent adds Phase 2 stubs while "we're here" | Strict file-set in this proposal; any new file outside `src/factor_mining/{__init__,operators,expression,grammar}.py` + `tests/logic/factor_mining/*.py` is a scope violation |
