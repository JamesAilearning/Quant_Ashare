## Implementation

- [x] Move executable risk-constraint behavior to an explicit experimental namespace and make the canonical core path fail closed.
- [x] Wire optional validated static taxonomy artifacts into Pipeline attribution config construction.
- [x] Add Tushare optional dependency metadata and update install hints.
- [x] Reject duplicate static taxonomy instruments before publisher IO.
- [x] Update docs/config comments where useful without changing default runtime behavior.

## Tests

- [x] Add/update risk-boundary and experimental risk-constraint tests.
- [x] Add/update Pipeline taxonomy attribution wiring tests.
- [x] Add/update taxonomy publisher duplicate-instrument tests.
- [x] Add/update Tushare dependency/install-hint tests.
- [x] Run `openspec validate --all --strict`.
- [x] Run `pytest -q -p no:cacheprovider tests/governance tests/logic`.
