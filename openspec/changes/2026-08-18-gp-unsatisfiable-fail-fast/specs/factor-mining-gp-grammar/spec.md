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
