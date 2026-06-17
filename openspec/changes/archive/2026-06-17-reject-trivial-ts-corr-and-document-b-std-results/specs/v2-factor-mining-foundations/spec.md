## ADDED Requirements

### Requirement: `ts_corr` SHALL reject trivial `(f(X), X)` forms at construction time

`OperatorCall("ts_corr", (a, b, n))` SHALL raise `GrammarError` at construction time when the two factor-argument expressions `a` and `b` are trivially related. Trivial forms SHALL be:

1. **Structural equality**: `a == b` (same Expression AST, e.g. `ts_corr($close, $close, 20)`). The cross-sectional correlation of a series with itself is mechanically 1.0 (or NaN per the existing v1 §5.2 ±Inf → NaN rule when the per-ticker variance is zero), carrying no signal.
2. **Bijective univariate transform**: `a` is `op(b)` where `op ∈ {"neg", "log_safe", "sqrt_safe"}` (the bijective monotonic univariate operators in the v1 registry), or symmetrically `b = op(a)`. The correlation between a series and a monotonic function of itself is mechanically near ±1 over a rolling window; residual variation is numerical compression artefact (e.g. `log` near zero), not predictive content.

`abs` and `sign` SHALL NOT be in the bijective-univariate set: `abs` legitimately captures sign-asymmetry between a series and its absolute value (which can be a real factor), and `sign` is piecewise-constant such that `ts_corr` is already degenerate (zero per-ticker variance → NaN per the existing rule). The rule rejection text SHALL cite `docs/factor_mining/empirical_results_b_std.md` §"Top expressions reveal pseudo-signals" so future contributors can see the empirical motivation.

#### Scenario: ts_corr of a feature with itself
- **WHEN** `OperatorCall("ts_corr", (Terminal("$close"), Terminal("$close"), Terminal("20")))` is constructed
- **THEN** `GrammarError` is raised with a message containing "trivially related"
- **AND** the message cites the empirical doc

#### Scenario: ts_corr of a feature with `neg`/`log_safe`/`sqrt_safe` of itself
- **WHEN** any of `OperatorCall("ts_corr", (OperatorCall(op, ($close,)), $close, 20))` is constructed for `op ∈ {"neg", "log_safe", "sqrt_safe"}`
- **THEN** `GrammarError` is raised at construction time
- **AND** the same rejection holds for the symmetric form `ts_corr($close, op($close), 20)`

#### Scenario: ts_corr of two distinct features
- **WHEN** `OperatorCall("ts_corr", (Terminal("$close"), Terminal("$volume"), Terminal("20")))` is constructed
- **THEN** no exception is raised (cross-feature correlation is a legitimate factor pattern)

#### Scenario: ts_corr of `abs(X)` with `X`
- **WHEN** `OperatorCall("ts_corr", (OperatorCall("abs", (Terminal("$close"),)), Terminal("$close"), Terminal("20")))` is constructed
- **THEN** no exception is raised (`abs` is intentionally not in the bijective-univariate blocklist)

#### Scenario: ts_corr of two unrelated operator subtrees
- **WHEN** `OperatorCall("ts_corr", (ts_mean($close, 20), ts_std($volume, 20), Terminal("20")))` is constructed
- **THEN** no exception is raised

## MODIFIED Requirements

### Requirement: Random expression generator SHALL produce 100% type-valid scale-pure expressions with min_depth ≥ 2

The random expression generator SHALL accept a `target_type: ExprType` argument and a `min_depth` argument with default 2. For any call with `target_type = ExprType("CSF", "PURE")`, every sample produced SHALL have `output_type == ExprType("CSF", "PURE")` AND a tree depth ≥ `min_depth`. A test SHALL exercise the generator with 1000 samples (fixed RNG seed, `max_depth=6`, `min_depth=2`, `target_type=ExprType("CSF", "PURE")`) and assert 100% type-valid and 100% scale-pure roots. The generator SHALL sample only `group_by=None` for `cs_*` operators in v1 (per `decisions.md` D2).

The generator MAY encounter operator-argument combinations that pass static type-checking but are rejected by additional `OperatorCall.__post_init__` invariants (e.g. the `ts_corr` trivial-form rule). In such cases the generator SHALL retry with freshly-sampled subtrees up to a bounded retry budget (currently 10), and SHALL fall back to a leaf when the retry budget is exhausted and a leaf is available for the requested target type. The retry budget SHALL never be the binding cause of generator failure under normal operation: any output-type that has at least one non-trivial operator candidate in the registry SHALL succeed.

#### Scenario: the 1000-sample generator test is executed
- **WHEN** `pytest tests/logic/factor_mining/test_grammar.py::test_random_generator_1000_samples` runs
- **THEN** all 1000 generated expressions have `output_type.kind == "CSF"` AND `output_type.taint == "PURE"`
- **AND** every expression has depth ≥ 2
- **AND** no generated `cs_*` operator carries `group_by != None`

#### Scenario: the generator is asked for a `FLOAT, ADJ_TAINTED` target deep in a subtree
- **WHEN** the recursive generator is called under a `cs_*` parent with `target_type = ExprType("FLOAT", "PURE")`
- **THEN** every candidate operator and leaf considered for that subtree has output `taint = PURE`
- **AND** `ADJ_TAINTED` leaves like `$close` are filtered out unless they reach `PURE` via a `div_safe` ratio in the subtree

#### Scenario: the generator samples a trivial ts_corr form internally
- **WHEN** the recursive generator picks `ts_corr` and the sampled children happen to be `(f(X), X, N)` for `f ∈ {"neg", "log_safe", "sqrt_safe"}`
- **THEN** the `OperatorCall` constructor raises `GrammarError`
- **AND** the generator catches the error and retries with new children up to 10 times
- **AND** in practice no random expression test (`test_random_generator_avoids_trivial_ts_corr`, 500 samples) exhausts the retry budget
