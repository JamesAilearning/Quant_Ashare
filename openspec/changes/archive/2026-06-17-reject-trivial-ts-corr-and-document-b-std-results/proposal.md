# Reject trivial `ts_corr(f(X), X, N)` pseudo-signals + document B-std empirical results

## Why

The first end-to-end empirical evaluation of the factor-mining
subsystem against the design doc's §10 IR success criterion (csi300,
2018-2025 PIT bundle, walk-forward bake-off vs Alpha158) failed in
all three configurations attempted (default fitness, soft fitness,
soft-top-20). Headline numbers:

| | Alpha158 | MinedFactor default | MinedFactor soft | MinedFactor soft-top-20 |
|---|---:|---:|---:|---:|
| IR | **+0.466** | -0.304 | -0.126 | -0.094 |
| IC_1d | **+0.0247** | -0.0020 | +0.0033 | +0.0060 |
| `design_doc_ir_threshold_met` | — | **FALSE** | **FALSE** | **FALSE** |

Manual review of the highest-IS-IC factors in the best (soft) pool
surfaced a **grammar-level pseudo-signal**: three of the top four
entries were `ts_corr(log_safe($close), $close, N)` or `ts_corr(log_safe($high), $high, N)`.
These compute correlations between a value and a monotonic univariate
function of itself — the correlation is mechanically near ±1 over a
rolling window, and the residual variation is from numerical
compression at low prices, not from predictive content. They score
high in IS-IC but contribute zero OOS signal, polluting the GP search.

This change ships two coupled deliverables:

1. **Grammar-level rejection** of trivial `ts_corr(a, b, N)` forms at
   construction time. Specifically: reject when `a` and `b` are
   structurally equal, or when one is `neg(X)` / `log_safe(X)` / `sqrt_safe(X)`
   of the other (the bijective monotonic univariate operators in the
   v1 op library). The check is structural (cheap, exact, no false
   positives on legitimate cross-feature correlations like
   `ts_corr($close, $volume, N)`). It runs in `OperatorCall.__post_init__`
   alongside the existing type-check.
2. **A new docs/factor_mining/empirical_results_b_std.md** documenting
   the empirical evaluation: experimental setup, headline numbers,
   root-cause analysis (three findings: novelty pressure crowds out
   signal; IS-OOS overfit; pseudo-signals + low diversity), and a
   ranked follow-up list (extend feature universe via Tushare
   `daily_basic`, reject pseudo-signals via grammar — this PR, update
   FitnessConfig defaults to soft values, larger GP budget, alternate
   universes).

### Why this is a single PR

The grammar fix is justified directly by the empirical evidence in
the doc — the doc cites the grammar rule as one of the concrete
follow-ups it motivates. Shipping them together makes the
provenance explicit: future operators reading the spec change see
why the rule exists, not just what it does.

### Why not also update FitnessConfig defaults in this PR

The soft-pool fitness weights are a separate, larger change with
broader implications (every existing GP run would behave differently
with new defaults). They warrant their own change so the spec delta
on `v2-factor-mining-foundations` "Phase 2 SHALL ship the default
fitness config locking cost_rate = 0.003" gets explicit review.
Tracked in the empirical_results_b_std.md follow-up list as item #3.

## What Changes

### `src/factor_mining/expression.py` — MODIFY `OperatorCall.__post_init__`

- Add `_BIJECTIVE_UNIVARIATE_OPS = {"neg", "log_safe", "sqrt_safe"}`
  module-level constant.
- Add `_ts_corr_is_trivial(a, b) -> bool` helper that returns True if
  `a == b` (structural equality) OR one is a `_BIJECTIVE_UNIVARIATE_OPS`
  unary of the other.
- In `OperatorCall.__post_init__`, after the existing type-check, if
  `op_name == "ts_corr"` and `_ts_corr_is_trivial(children[0], children[1])`,
  raise `GrammarError` with a message citing the empirical doc and the
  rule rationale.
- `abs` and `sign` are NOT in the blocklist: `abs` legitimately
  captures sign-asymmetry; `sign` is piecewise-constant which the
  existing `ts_corr` ±Inf → NaN rule already handles.

### `src/factor_mining/grammar.py` — MODIFY `_random_operator`

- Wrap the `OperatorCall(op.name, children)` construction in a
  retry loop (`MAX_OP_RETRIES = 10`). When the constructor rejects
  (e.g. the new pseudo-signal rule), resample children and try again.
- Fall back to a leaf if available after the retry budget is exhausted;
  raise `GrammarError` otherwise. In practice the rejection rate of
  the trivial form is < 1%, so the budget is never exhausted (verified
  in the 500-sample regression test below).

### `tests/logic/factor_mining/test_grammar.py` — ADD regressions

- `test_ts_corr_rejects_same_expression_twice` — `ts_corr($close, $close, 20)` raises.
- `test_ts_corr_rejects_monotonic_univariate_of_self[neg|log_safe|sqrt_safe]` (parametrised).
- `test_ts_corr_rejects_self_then_monotonic[neg|log_safe|sqrt_safe]` (symmetric form).
- `test_ts_corr_accepts_two_different_features` — `ts_corr($close, $volume, 20)` is OK.
- `test_ts_corr_accepts_abs_of_self` — `abs` not in the blocklist.
- `test_ts_corr_accepts_unrelated_expressions` — `ts_corr(ts_mean($close, 20), ts_std($volume, 20), 20)` OK.
- `test_random_generator_avoids_trivial_ts_corr` — sample 500 random
  expressions, none triggers the rule (proves the retry loop works).

### `docs/factor_mining/empirical_results_b_std.md` — NEW file

The empirical evaluation writeup. Documents:
- Scope and assumptions (PIT bundle 2018-2025, csi300, 23 folds, GP train 2018-2023)
- Experimental matrix (default / soft / soft-top-20)
- Headline numbers table
- Root-cause analysis (3 findings: novelty dominance, IS-OOS gap, pseudo-signals + diversity)
- What worked / what did not
- Concrete follow-ups ranked by expected impact

### Spec delta

`v2-factor-mining-foundations` — MODIFY the existing "Random expression generator SHALL produce 100% type-valid scale-pure expressions with min_depth ≥ 2" requirement to acknowledge that the constructor MAY reject additional non-type-but-still-grammar-level invariants (the new pseudo-signal rule is the first instance), and the generator SHALL retry-with-resample when this happens.

ADD a new requirement: `ts_corr` SHALL reject trivial forms at construction time.

## Non-Goals

- **No change to FitnessConfig defaults.** The empirical doc
  recommends them but the actual config change is a separate
  proposal (would otherwise break determinism of every existing GP
  run's saved artefacts).
- **No new operators or features added.** The "extend feature
  universe with daily_basic" follow-up is the highest-impact item
  in the empirical doc, but it requires Tushare endpoint work + PIT
  bundle rebuild + grammar.FeatureRegistry extension. Separate epic.
- **No second-order trivial-form detection.** `ts_corr(div_safe(X, X), X, N)`
  (where the inner div_safe is a constant 1.0) is also a pseudo-signal
  but requires deeper semantic analysis. Out of scope; if it shows
  up in future pool reviews it can be added.
- **No automatic re-mining or pool re-promotion.** The existing
  research/mined_factors/runs/* pools are unaffected (operator
  re-mines if they want a pool free of the rejected forms).
- **No `ts_cov` rule update** — `ts_cov` is not in the v1 operator
  library (per `scale_invariance.md` §4). If/when v2 adds it, the
  same check should apply.
