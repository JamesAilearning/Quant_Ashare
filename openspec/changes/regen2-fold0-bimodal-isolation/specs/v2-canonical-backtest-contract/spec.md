## MODIFIED Requirements

### Requirement: The walk-forward regression baseline SHALL be replay-anchored

The committed walk-forward regression baseline SHALL be reproducible by a
DETERMINISTIC frozen-score replay — replaying fixed per-fold prediction Series
through the canonical `BacktestRunner` at the official semantics (T+1 execution,
close-derived price limits, PIT ST exclusion) — WITHOUT retraining any model or
rebuilding the bundle. On the project's canonical dependency stack (the pyproject pin:
`numpy<2`, `scipy<1.14`, `pandas<2.3`) the replay SHALL reproduce to machine precision
the STRICT surface — folds 1-22 (all metrics) AND fold-0's information coefficients —
and the regression test SHALL hold that 1e-6 tolerance in TEST SOURCE (not in the
fixture) so a tampered fixture cannot widen its own gate. The baseline SHALL be
GENERATED on that canonical stack — a gen-env==canonical-pin assertion SHALL fail
generation loud off-pin.

fold-0 is a DEGENERATE fold (its frozen scores collapse to ~39 buckets, so the top-k
cutoff lands inside a tie block) whose three TOPK-DEPENDENT backtest metrics (return,
drawdown, information ratio) — and the aggregate keys derived from the per-fold IR/ann
set — are PER-RUNNER BIMODAL even on the canonical pin: the tie-break flips between
exactly two selections, fixed for a whole CI run but varying between runners (a discrete
byte-identical flip, not continuous FP noise). The regression test SHALL assert those
metrics against the two KNOWN selections (the committed value OR a recorded alternate,
both in test source) so a THIRD value still fails as a real regression, WITHOUT widening
the 1e-6 tolerance. This is a documented known-limitation; the deterministic fix (a
stable secondary sort key) changes the selection and is deferred to phase-6.

The committed baseline JSON SHALL carry, alongside the numbers, the corrected
semantics, the statistical caveat (the headline is within cross-fold noise — NOT a
strategy improvement and not predictive of live performance), and the BENCHMARK
BASIS: the canonical baseline measures excess against the **SH000300TR total-return**
index. The total-return benchmark is APPLIED (it supersedes the SH000300 price-index
basis used by the preserved REGEN-A control; the prior "deferral" is CLOSED). A
CI-runnable governance test SHALL pin that this framing — including the total-return
basis — is committed with the value.

#### Scenario: a deterministic replay reproduces the committed strict surface
- **WHEN** the frozen-score replay runs against the same bundle on the canonical
  dependency stack
- **THEN** folds 1-22 (all metrics) and fold-0's ICs reproduce within the in-source
  tight tolerance, else the regression test fails

#### Scenario: fold-0's per-runner-bimodal backtest metrics accept either known selection
- **WHEN** the replay runs on a CI runner that flips fold-0's degenerate tie-break
- **THEN** fold-0's topk-dependent backtest metrics — and the aggregate keys they feed —
  reproduce the committed value OR the recorded alternate; a third value fails

#### Scenario: the corrected value cannot be committed without its framing
- **WHEN** the committed baseline JSON holds the canonical headline IR
- **THEN** a CI-runnable pin requires the corrected-semantics provenance, the
  within-noise statistical caveat, and the total-return-benchmark basis (excess
  measured against SH000300TR) to be present, else CI fails

#### Scenario: no single-fold anchor
- **WHEN** the walk-forward regression suite runs
- **THEN** the anchor is the full multi-fold deterministic replay, not a single fold
