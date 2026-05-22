# Tasks: Factor Mining Foundations (Phase 1)

## OpenSpec (this proposal — propose stage)

- [x] Draft `proposal.md` (Why / What Changes / Non-Goals)
- [x] Draft `design.md` (citing `scale_invariance.md` as normative,
      enumerating operator catalogue, taint table, root rule, eight
      pinned examples, random-generator contract, D5 gate)
- [x] Draft `tasks.md` (this file)
- [x] Draft `specs/v2-factor-mining-foundations/spec.md` with ADDED
      Requirements covering module placement, no-data gate, operator
      catalogue, numerical stability + PIT-gap respect, commutative
      hash, expression AST + round-trip, type system, feature
      universe, scale-invariance gate, random generator contract
- [x] Draft `specs/v2-project-skeleton-boundaries/spec.md` with the
      MODIFIED Requirement that adds `src/factor_mining/` to the
      production-layer skeleton (per `decisions.md` O1) while
      preserving the unchanged `research/factor_lab/` non-production
      contract
- [x] `openspec validate add-factor-mining-operators --strict` — green
- [x] User reviews proposal / design / tasks / spec deltas — OK to apply

## Phase 1 Implementation (apply stage — after user OK)

### 1.1 `src/factor_mining/__init__.py`
- [x] Re-export public surface: `Expression`, `Terminal`, `OperatorCall`,
      `ExprType`, `OutputKind`, `ScaleTaint`, `Operator`, `REGISTRY`,
      `WINDOW_LITERALS`, `parse_expression`, `random_expression`,
      `GrammarError`, `FeatureRegistry`.
- [x] No side effects (the operator registry is constructed at
      module load via ``_register_all()`` once, then used as a value).

### 1.2 `src/factor_mining/operators.py`
- [x] 28 CPU operator implementations per `design.md` §"Operator
      Catalogue":
      - Arithmetic: `add`, `sub`, `mul`, `div_safe`
      - Unary: `neg`, `abs`, `sign`, `log_safe`, `sqrt_safe`
      - Time-series: `ts_mean`, `ts_std`, `ts_max`, `ts_min`,
        `ts_sum`, `ts_delta`, `ts_pctchange`, `ts_rank`, `ts_argmax`,
        `ts_argmin`, `ts_corr`, `ts_skew`, `ts_kurt`, `ts_decay_linear`
      - Cross-sectional: `cs_rank`, `cs_zscore`, `cs_demean`,
        `cs_winsorize`
      - Conditional: `where`
- [x] Every `ts_*` uses pandas rolling with `min_periods = window`
      (no partial-window leaks).
- [x] `div_safe` returns NaN for zero / near-zero denominators
      (never ±Inf).
- [x] `log_safe`, `sqrt_safe` return NaN for inputs ≤ 0 (no raise).
- [x] `ts_rank` returns 0.5 for constant windows.
- [x] `cs_zscore` returns 0 (not NaN) for per-day std ≈ 0.
- [x] `ts_corr` replaces ±Inf with NaN.
- [x] No `qlib` import, no qlib data API call, no qlib bootstrap, no
      `PITDataProvider` reference.

### 1.3 `src/factor_mining/expression.py`
- [x] `Terminal` and `OperatorCall` frozen dataclasses; both subclass
      a common `Expression` base.
- [x] `Expression.to_dict()` / `Expression.from_dict()` round-trip;
      `parse_expression(qlib_str)` parses the human-readable form.
- [x] `Expression.to_qlib_string()` pretty-prints; result is stable
      across round-trip.
- [x] `__hash__` is structural, bottom-up; commutative operators
      (`add`, `mul`) **sort child hashes before hashing**.
- [x] `__eq__` is structural (compares kind + name + children with
      commutativity baked in).
- [x] `__post_init__` triggers type-check via the operator registry;
      illegal constructions raise `GrammarError`.

### 1.4 `src/factor_mining/grammar.py`
- [x] `OutputKind` and `ScaleTaint` `Literal` types.
- [x] `ExprType(kind, taint)` frozen dataclass with `taint="PURE"`
      default.
- [x] `FeatureRegistry.V1 = ("$open", "$high", "$low", "$close",
      "$volume", "$money")` per `decisions.md` D3.
- [x] `OperatorRegistry` carrying every operator's
      `output_type(*input_types) -> ExprType` per `scale_invariance.md`
      §4 propagation table.
- [x] `random_expression(target_type, max_depth, min_depth=2, rng)` —
      taint-aware recursive generator built on a backward-chained
      lookup table.
- [x] `GrammarError` exception class.

### 1.5 `tests/logic/factor_mining/test_operators.py`
- [x] Per-operator matrix: normal input, NaN input, zero input,
      negative input (log/sqrt), constant input, single row, empty
      input, **PIT-gap input** (NaN hole that MUST NOT be bridged).
- [x] Assert `min_periods = window` behaviour on every `ts_*`.
- [x] Assert `div_safe(x, 0) == NaN` (not Inf).
- [x] Assert `cs_zscore(constant_series) == 0` (not NaN).

### 1.6 `tests/logic/factor_mining/test_expression.py`
- [x] Serialisation round-trip: `Expression.from_dict(expr.to_dict()) == expr`.
- [x] Hash stability across round-trip.
- [x] **Commutative hash equality**: `add($close, $volume)` and
      `add($volume, $close)` hash identically; same for `mul`.
- [x] Illegal construction (e.g. `add($close, $volume)` — `(ADJ,
      PURE)` is ILLEGAL for add) raises `GrammarError`.

### 1.7 `tests/logic/factor_mining/test_grammar.py`
- [x] 1000 random expressions generated with `target_type=ExprType("CSF", "PURE")`,
      `max_depth=6`, `min_depth=2`, fixed RNG seed.
- [x] Assert 100% of samples have `expr.output_type == ExprType("CSF", "PURE")`.
- [x] Assert 100% of samples have depth ≥ 2.
- [x] Assert v1 cs_* compute functions do NOT expose `group_by`
      (`decisions.md` D2 — hook deferred to v2; AST has no way to
      represent `group_by != None` in v1).

### 1.8 `tests/logic/factor_mining/test_scale_invariance.py`
- [x] Eight pinned pass/fail examples from `scale_invariance.md` §5
      (verbatim parametrised; verdict matches the table).
- [x] Additional rejected forms enumerated below the §5 table:
      `cs_rank(add($close, $open))`, `cs_rank(mul($close, $volume))`,
      `cs_rank(ts_mean($close, 20))`.
- [x] Terminal taint table assertions (`$close.taint == "ADJ_TAINTED"`,
      `$volume.taint == "PURE"`, etc.).

### 1.9 `tests/logic/factor_mining/test_integration_smoke.py`
- [x] Hand-build `cs_rank(div_safe(ts_delta($close, 20), $close))`.
- [x] Assert `output_type == ExprType("CSF", "PURE")`.
- [x] Round-trip through `to_dict()` / `from_dict()`; hashes equal.
- [x] Pretty-print result is stable across round-trip.

## Validation (apply stage)

- [x] `pytest tests/logic/factor_mining/ -v` — all 137 green
- [x] `ruff check src/factor_mining/ tests/logic/factor_mining/` — green
- [x] `python -c "import src.factor_mining; import src.factor_mining.operators; import src.factor_mining.expression; import src.factor_mining.grammar"` — succeeds
- [x] `grep -rn "qlib\.data\|qlib\.init\|from qlib" src/factor_mining/`
      — **zero matches** (D5 strict gate)
- [x] `openspec validate add-factor-mining-operators --strict` — green
- [x] Phase 1 acceptance criteria from
      `factor_mining_phase1_preflight.md` §6 — reviewed and satisfied
      (with the operator-count discrepancy noted: 28 in v1 per
      `scale_invariance.md` §4, vs the preflight's approximate "22")

## Phase Gate

Phase 2 (`add-factor-mining-evaluator`) does NOT begin until:

- This change is archived to `openspec/specs/v2-factor-mining-foundations/`.
- All tests in `tests/logic/factor_mining/` are green on `main`.
- A user-confirmed sign-off message marks Phase 1 complete.

## Deferred (NOT this proposal)

- Phase 2: `pit_adapter.py`, `evaluator.py`, `fitness.py`,
  `factor_pool.py`, PIT data wiring, IC computation against the
  hand-built 20-day reversal, `default.yaml` config write.
- Phase 3: `gp_engine.py`, `miner.py`, smoke config.
- Phase 4: GPU kernels.
- Phase 5: `MinedFactorHandler` registration into
  `v2-feature-handler-registry`.
- Phase 6: validator, promotion CLI, walk-forward integration,
  user-facing docs in `docs/factor_mining/user_guide.md`.
- D5 grep guard wired into `.githooks/pre-commit` (Phase 2 task).
- `cost_rate = 0.003` written into `config/factor_mining/default.yaml`
  (Phase 2 / Phase 3 task).
- `research/mined_factors/{runs,candidates,production}/` directory
  scaffold (Phase 5 task).
