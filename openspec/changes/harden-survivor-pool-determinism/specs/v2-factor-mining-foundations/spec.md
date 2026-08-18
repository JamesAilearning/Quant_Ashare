# v2-factor-mining-foundations — delta

## ADDED Requirements

### Requirement: The survivor pool SHALL be constructed deterministically

The survivor pool SHALL depend only on its inputs — never on interpreter
state. `filter_correlated` produces the promotion gate's treatment arm,
so a pool that varies between executions makes the adjudication
irreproducible.

Scan order SHALL be fitness descending, ties broken by a STABLE digest of
the expression's canonical string. `hash()`-derived values (`expr_hash`)
SHALL NOT be used for any ordering that decides which factor is kept:
they vary across interpreter processes and `PYTHONHASHSEED`, so identical
artifacts would retain different correlated survivors on separate runs.

#### Scenario: the same artifacts are filtered twice in different processes
- **WHEN** `filter_correlated` runs twice on identical inputs in
  interpreter processes with different hash seeds
- **THEN** the retained survivor set and its order are identical

#### Scenario: two equally-fit correlated survivors compete
- **WHEN** two survivors share a fitness value and exceed the correlation
  threshold against each other
- **THEN** which one is kept is decided by the canonical-string digest,
  reproducibly

### Requirement: Correlation filtering that cannot be performed SHALL refuse

Filtering SHALL refuse when a correlation cannot be computed, and a factor whose correlation against the already-kept set could not be computed SHALL NOT be kept. "Could not filter" SHALL NOT read as "passed
the filter" — the permissive direction silently changes the pool that
adjudicates promotion.

This covers all three ways the computation can fail to happen: an
evaluation error, a non-frame result, and a pair whose jointly-finite
overlap is below the correlation primitive's minimum (a skipped pair
contributes 0.0, which is indistinguishable from genuine independence).

#### Scenario: evaluation raises while filtering
- **WHEN** evaluating a passing survivor for correlation raises
- **THEN** filtering refuses with an error naming the expression
- **AND** the factor is NOT carried into the survivor pool

#### Scenario: a pair carries a degenerate cell but ample finite overlap
- **WHEN** a pair contains a non-finite cell yet still has at least the
  minimum jointly-finite observations
- **THEN** the correlation is computed on the finite subset and the pair
  is NOT refused
- **AND** a single degenerate cell never reads as "uncorrelated"

#### Scenario: a pair has insufficient jointly-finite overlap
- **WHEN** a survivor shares fewer than the minimum jointly-finite cells
  with any already-kept factor
- **THEN** filtering refuses rather than treating the uncomputed pair as
  uncorrelated

### Requirement: An established terminal pool SHALL NOT widen

An engine's terminal pool, once established by a run, SHALL NOT widen.
The pool that run bred under is a property of that run. Later operations on the same engine — resuming, scoring an
injected expression, restoring a checkpoint — SHALL NOT derive a wider
pool from whatever panel they are handed.

`_allowed_terminals is None` AFTER a run denotes the V1 default pool, not
an unknown pool; a consumer SHALL resolve that sentinel rather than treat
the engine as fresh.

#### Scenario: a resumed run is given a different panel
- **WHEN** an engine that already ran is resumed on a panel whose terminal
  set differs from the one it bred under
- **THEN** the run refuses, because one experiment cannot span two search
  spaces
- **AND** resuming on the same panel proceeds unchanged

#### Scenario: an injected expression is scored after a run
- **WHEN** `score_expression` receives an expression referencing a terminal
  outside the pool the run bred under
- **THEN** scoring refuses, because a score under a configuration that
  cannot breed the expression is not that run's marginal contribution

#### Scenario: a checkpoint is restored and scored before re-running
- **WHEN** an engine is restored from a checkpoint written after a run
- **THEN** it carries that run's terminal pool and the established-run
  marker
- **AND** scoring against a wider panel still refuses

#### Scenario: a checkpoint predates terminal-pool recording
- **WHEN** a checkpoint carries neither the established-run marker nor a
  recorded terminal pool
- **THEN** loading refuses, because the restored population and caches
  demonstrably came from a run whose search space cannot be recovered
- **AND** treating it as a fresh engine is NOT permitted: that skips the
  pool guard entirely, which is the permissive direction
