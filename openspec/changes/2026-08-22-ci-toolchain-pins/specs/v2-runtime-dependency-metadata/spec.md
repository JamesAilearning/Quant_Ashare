# Delta for v2-runtime-dependency-metadata

## ADDED Requirements

### Requirement: Every dependency CI installs SHALL declare an upper version bound

Project metadata SHALL declare an upper version bound for every dependency the
CI workflows install — the base dependencies AND every extra they name — and a
test SHALL enforce this by deriving the covered groups from the workflows' own
install lines rather than from a hand-written list. Dependencies that JUDGE the
code (the test runner and its plugins, the type checker, the linter) SHALL be
bounded at the NEXT minor version; libraries the code merely uses MAY be bounded
at the major version.

An unbounded judging tool turns CI red on the day it publishes, independently
of the change under review. Measured: PR #462 went red on all six legs because
`pytest>=7.4` resolved to a same-day 9.1.1 whose logging plugin behaves
differently from the 9.0.3 a developer had locally — local green proved
nothing.

A major-version bound would not have prevented it: the break came from a MINOR
bump. Nor does merely requiring a dotted ceiling — `<10.0` is still a
major-only bound wearing a minor's clothes — so the ceiling is required to be
exactly the floor's next minor.

`pip install -e ".[dev,ui]"` resolves the base dependency list as well as the
named extras, so a guard that walks only the extras leaves the base list free to
drift while reporting success.

Runtime dependencies already carried upper bounds for numpy / scipy / pandas for
exactly this reason; the gap was that the reasoning had never been extended to
the rest of the base list, nor to the tools that judge.

#### Scenario: an unbounded dependency in anything CI installs fails the build
- **WHEN** any requirement in the base list, or in an extra a workflow installs,
  carries no upper bound
- **THEN** the governance test fails and names the requirement

#### Scenario: the covered groups follow the workflows
- **WHEN** a workflow changes which extras it installs
- **THEN** the enforced set follows it, because the test reads the install lines
  instead of restating the group names

#### Scenario: a major-only bound on a judging tool is rejected
- **WHEN** the test runner, one of its plugins, the type checker, or the linter
  is bounded above its floor's next minor — including a dotted form such as
  `<10.0`
- **THEN** the governance test fails, because the failure this rule exists for
  was a minor-version bump

### Requirement: A dependency window restated outside project metadata SHALL be pinned to it

Any dependency window restated outside `pyproject.toml` SHALL be kept
byte-identical to the declaration, enforced by a test that scans EVERY workflow
rather than a named one.

The numpy and scipy windows are inlined in the CI workflows because qlib is
installed BEFORE the project and therefore cannot pick the constraint up from
project metadata. Restating is necessary; drifting apart silently is not — the
REGEN-2 determinism anchor reproduces only inside that window, so a workflow
that installs a different one would move the anchor without any spec changing.

Naming one workflow is not enough: the window is restated in the test workflow
AND in the baseline-regeneration workflow, and the regeneration path is the one
that actually produces the anchor.

#### Scenario: every workflow that restates a window names the same one
- **WHEN** any workflow's numpy or scipy constraint differs from
  `pyproject.toml`
- **THEN** the governance test fails and names the workflow

#### Scenario: the scan covers workflows nobody listed
- **WHEN** a new workflow restates one of these windows
- **THEN** it is checked automatically, because the test discovers workflows
  instead of naming them
