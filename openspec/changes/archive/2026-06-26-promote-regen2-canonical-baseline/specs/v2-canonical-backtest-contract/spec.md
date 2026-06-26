## MODIFIED Requirements

### Requirement: The walk-forward regression baseline SHALL be replay-anchored

The committed walk-forward regression baseline SHALL be reproducible by a
DETERMINISTIC frozen-score replay — replaying fixed per-fold prediction Series
through the canonical `BacktestRunner` at the official semantics (T+1 execution,
close-derived price limits, PIT ST exclusion) — WITHOUT retraining any model or
rebuilding the bundle. The replay SHALL reproduce the committed aggregate AND
per-fold metrics to machine precision ON THE PROJECT'S CANONICAL DEPENDENCY STACK
(the pyproject pin: `numpy<2`, `scipy<1.14`, `pandas<2.3`), and the regression test
SHALL hold that tolerance in TEST SOURCE (not in the fixture) so a tampered fixture
cannot widen its own gate. The baseline SHALL be GENERATED on that canonical stack —
a gen-env==canonical-pin assertion SHALL fail generation loud off-pin — because a
degenerate fold's top-k tie-break is numpy-major-sensitive (a baseline baked on an
off-pin stack would not reproduce in CI).

The committed baseline JSON SHALL carry, alongside the numbers, the corrected
semantics, the statistical caveat (the headline is within cross-fold noise — NOT a
strategy improvement and not predictive of live performance), and the BENCHMARK
BASIS: the canonical baseline measures excess against the **SH000300TR total-return**
index. The total-return benchmark is APPLIED (it supersedes the SH000300 price-index
basis used by the preserved REGEN-A control; the prior "deferral" is CLOSED). A
CI-runnable governance test SHALL pin that this framing — including the total-return
basis — is committed with the value.

#### Scenario: a deterministic replay reproduces the committed baseline
- **WHEN** the frozen-score replay runs against the same bundle on the canonical
  dependency stack
- **THEN** every committed aggregate and per-fold metric reproduces within the
  in-source tight tolerance, else the regression test fails

#### Scenario: the corrected value cannot be committed without its framing
- **WHEN** the committed baseline JSON holds the canonical headline IR
- **THEN** a CI-runnable pin requires the corrected-semantics provenance, the
  within-noise statistical caveat, and the total-return-benchmark basis (excess
  measured against SH000300TR) to be present, else CI fails

#### Scenario: no single-fold anchor
- **WHEN** the walk-forward regression suite runs
- **THEN** the anchor is the full multi-fold deterministic replay, not a single fold
