# Tasks: Isolate fold-0's per-runner-bimodal metrics in the REGEN-2 replay anchor

## OpenSpec (propose stage)

- [x] Draft proposal.md / tasks.md
- [x] Draft `specs/v2-canonical-backtest-contract/spec.md` MODIFIED delta
- [x] `openspec validate regen2-fold0-bimodal-isolation --strict` green

## Implementation

- [x] `tests/regression/test_walk_forward_replay_baseline_regen2.py`: `_reproduces()` +
      `_KNOWN_FOLD0_BACKTEST_ALT` + `_KNOWN_AGGREGATE_ALT`; per-fold + aggregate checks
      assert fold-0's topk-dependent metrics against {committed OR known alternate},
      strict 1e-6 everywhere else (folds 1-22, fold-0 ICs, stable aggregate keys)
- [x] docstring corrected (supersedes the wrong "run-to-run flake + 3-attempt retry")
- [x] `.github/workflows/test.yml`: remove the dead in-run retry; plain single run

## Verify

- [x] offline logic check vs the committed JSON: A reproduces; B-flip accepted; a fold-1
      regression / fold-0-third-value / aggregate-third-value / fold-0-IC drift all FAIL
- [x] ruff clean; test collects; workflow yaml valid
- [ ] CI: the REGEN-2 leg is GREEN on any runner (the actual replay runs in CI)
