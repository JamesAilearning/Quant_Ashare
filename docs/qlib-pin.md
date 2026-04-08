# qlib Dependency Pin

V2 consumes qlib from a local source checkout, not from PyPI.

## Local path

- `D:/Qlib/qlib`

## Pinned commit

- `8fd6d5ca7eab59a69bf5c62f84e8b4e2a4e8910b` (recorded 2026-04-08)

## Compatible data bundle

- `D:/qlib_data/my_cn_data` (cn region, day calendar)

## Upgrade procedure

1. Open a new OpenSpec change (`upgrade-qlib-pin-<yyyy-mm-dd>`).
2. Record the new commit hash in this file.
3. Run the full test suite, including the governance regression tests that
   assert `CANONICAL_OFFICIAL_BACKTEST_CALLABLE is qlib.backtest.backtest`.
4. Only archive the change after all tests pass.

## Why not PyPI

The project intentionally depends on a local source checkout so that
the exact qlib version in use is always auditable and reproducible
from a single commit hash. This matches the run-artifact contract's
`code_ref` / `config_fingerprint` requirements.
