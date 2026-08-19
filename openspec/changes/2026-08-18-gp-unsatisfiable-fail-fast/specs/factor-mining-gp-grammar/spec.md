# Delta for factor-mining-gp-grammar

## ADDED Requirements

### Requirement: An unsatisfiable generator configuration SHALL be refused before sampling

`random_expression` SHALL determine, before drawing from the RNG, whether
the requested target type can be produced at all under the supplied
terminal whitelist, operator pool and depth budget. When it provably
cannot, the call SHALL raise `GrammarError` immediately.

The current generator instead retries `MAX_OP_RETRIES` at **every** depth
level with subtree generation inside the retry, so an unsatisfiable
configuration costs `MAX_OP_RETRIES ** max_depth` generator calls — 10⁶ at
the default `max_depth=6`. Measured: one such call takes ~11.6 s, and a
single regression test that exercises this path takes **38.8 minutes**.

The check SHALL be **conservative**: it refuses only configurations it can
prove admit no expression, and otherwise defers to the existing sampling
path unchanged. A false refusal would silently narrow a campaign's search
space — strictly worse than being slow, because the resulting pool would
misrepresent the pre-registered experiment.

The check SHALL NOT consume randomness. On every satisfiable
configuration the RNG draw sequence SHALL be byte-identical to the
current implementation, because seeded reproducibility is a pinned
invariant of this subsystem.

Refusal SHALL remain a `GrammarError` naming what the whitelist excludes,
so the failure stays as loud as it is today — only faster.

The check covers **type-level** unsatisfiability only. Constructor-level
exclusions — the AST post-validation in `expression.py`, such as
`_ts_corr_is_trivial` rejecting a correlation whose two operands are
structurally identical — are NOT modelled, and such configurations SHALL
keep taking the ordinary sampling path.

That limit is deliberate, not an oversight. Mirroring constructor rules
inside the reachability model would make the check a second copy of a
judgement that already lives in `expression.py`: every future validation
rule would have to be added in both places or the copy silently drifts.
Worse, the failure mode inverts — today the check errs by **refusing too
little** (safe: the caller falls back to sampling), whereas a mis-modelled
constructor rule would refuse **too much**, silently narrowing a
campaign's search space. Buying completeness at the price of that risk is
a bad trade for this subsystem.

Measured cost of the residual case, at the default `max_depth=6`:
`allowed_terminals={"$close"}` with `allowed_operators={"ts_corr",
"cs_rank"}` raises after 188,525 generator calls in **5.05 s** — against
0.0001 s when the type-level check does fire, and against the 38.8-minute
pathology this change removes. Slow-but-correct, on the conservative side.

#### Scenario: a constructor-level dead end is not pre-refused

- **GIVEN** a whitelist that type-checks but whose only expressions are
  rejected by the AST constructor (e.g. a single feature making every
  `ts_corr` trivially self-correlated)
- **WHEN** generation runs
- **THEN** the precheck does NOT refuse it; the ordinary sampling path
  raises as before

#### Scenario: a whitelist admitting no registered terminal is refused at once

- **GIVEN** a terminal whitelist whose intersection with the registry is empty
- **WHEN** `random_expression` is called for the canonical CSF target
- **THEN** it raises `GrammarError` without exhausting the retry budget

#### Scenario: a satisfiable campaign is untouched

- **GIVEN** a whitelist that admits at least one registered terminal
- **WHEN** expressions are generated from a fixed seed
- **THEN** the produced expressions are identical to those the previous
  implementation produced from that seed

#### Scenario: a partially restrictive whitelist still generates

- **GIVEN** a whitelist admitting terminals of only one taint, so some
  operator candidates have an empty input pool
- **WHEN** generation runs
- **THEN** it still succeeds by retrying other candidates — the check
  SHALL NOT refuse this case
