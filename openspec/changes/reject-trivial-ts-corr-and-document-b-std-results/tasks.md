# Tasks: Reject trivial ts_corr + document B-std empirical results

## OpenSpec (propose stage)

- [x] Draft proposal.md / tasks.md
- [x] Draft `specs/v2-factor-mining-foundations/spec.md` delta
- [x] `openspec validate reject-trivial-ts-corr-and-document-b-std-results --strict` green

## Implementation

- [x] `src/factor_mining/expression.py`:
  - [x] `_BIJECTIVE_UNIVARIATE_OPS` constant
  - [x] `_ts_corr_is_trivial(a, b)` helper
  - [x] `OperatorCall.__post_init__` adds the pseudo-signal check
- [x] `src/factor_mining/grammar.py::_random_operator`:
  - [x] Retry-with-resample loop (`MAX_OP_RETRIES=10`)
  - [x] Fallback to leaf when retries exhausted

## Tests

- [x] `test_ts_corr_rejects_same_expression_twice`
- [x] `test_ts_corr_rejects_monotonic_univariate_of_self[neg|log_safe|sqrt_safe]` (parametrised)
- [x] `test_ts_corr_rejects_self_then_monotonic[neg|log_safe|sqrt_safe]` (parametrised)
- [x] `test_ts_corr_accepts_two_different_features`
- [x] `test_ts_corr_accepts_abs_of_self`
- [x] `test_ts_corr_accepts_unrelated_expressions`
- [x] `test_random_generator_avoids_trivial_ts_corr` (500 samples; retry loop covers all)

## Documentation

- [x] `docs/factor_mining/empirical_results_b_std.md` (new) — full B-std evaluation writeup

## Validation

- [x] `pytest tests/logic/factor_mining/test_grammar.py -q` — 30/30
- [x] `pytest tests/logic/factor_mining/ tests/logic/test_mined_factor_handler.py -q` — 302/302
- [x] `pytest tests/logic/ -q` — 1145 passed, 18 skipped, 4 warnings, 34 subtests
- [x] `ruff check src/ tests/ scripts/` — green
- [x] D5 grep zero matches under `src/factor_mining/`
- [x] `openspec validate reject-trivial-ts-corr-and-document-b-std-results --strict` — green
- [ ] CI green on push (no `--admin` merge)

## Deferred (NOT this proposal)

- FitnessConfig default tuning (separate PR).
- daily_basic / fundamental feature universe extension (separate epic).
- Larger GP budget (pop=500, gen=50) — operator decision, no code change.
- Re-running B-std bake-off after this rule lands — the existing pools
  are tagged with their config snapshot; operators can re-mine if they
  want trivial-free pools.
