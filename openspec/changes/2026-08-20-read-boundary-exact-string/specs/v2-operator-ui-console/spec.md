# Delta for v2-operator-ui-console

## ADDED Requirements

### Requirement: The read boundary judges the exact recorded path string

The inspectability verdict SHALL examine the recorded `output_dir` exactly
as stored, and SHALL NOT parse a normalised form of it. Whitespace may be
stripped to decide whether a value was given at all; the stripped text
SHALL NOT be what is anchored, folded, or matched against the roots.

Leading whitespace is a valid filename character — on POSIX by definition,
and measured on this operator's Windows box a directory named `" output"`
is creatable — and the writer hands `config.output_dir` to `Path`
verbatim. Stripping first therefore judges a different directory than the
run wrote to: artifacts at `<repo>/ output/runs/x` lie outside the
boundary, while the stripped `<repo>/output/runs/x` lies inside, so the row
is listed and its detail action opens **another run's** artifacts. That is
the direction this boundary states must never happen.

A value that is only whitespace SHALL still count as absent, as before.

#### Scenario: a recorded directory whose name begins with a space

- **GIVEN** a catalog row with `output_dir` of `" output/runs/x"`
- **WHEN** the jobs page decides whether to list it
- **THEN** the row is treated as outside the boundary and set aside

#### Scenario: whitespace-only stays absent

- **GIVEN** a catalog row whose `output_dir` is only whitespace
- **WHEN** the inspectability verdict is taken
- **THEN** the row is not inspectable
