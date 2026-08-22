# Delta for v2-runtime-dependency-metadata

## ADDED Requirements

### Requirement: Every dependency CI installs SHALL declare an upper version bound

Project metadata SHALL declare an upper version bound for every dependency the
CI workflow installs, and a test SHALL enforce this by deriving the covered
extras from the workflow's own install line rather than from a hand-written
list. Dependencies that JUDGE the code — the test runner, the type checker, the
linter — SHALL be bounded at the minor version; libraries the code merely uses
MAY be bounded at the major version.

An unbounded judging tool turns CI red on the day it publishes, independently
of the change under review. Measured: PR #462 went red on all six legs because
`pytest>=7.4` resolved to a same-day 9.1.1 whose logging plugin behaves
differently from the 9.0.3 a developer had locally — local green proved
nothing.

A major-version bound would not have prevented it: the break came from a MINOR
bump. That is why the two classes of dependency get different granularity, and
why the minor granularity is itself asserted for the judging tools.

Runtime dependencies already carried upper bounds for exactly this reason; the
gap was that the reasoning had never been extended to the tools that judge.

#### Scenario: an unbounded dependency in a CI-installed extra fails the build
- **WHEN** any requirement in an extra the workflow installs carries no upper
  bound
- **THEN** the governance test fails and names the requirement

#### Scenario: the covered extras follow the workflow
- **WHEN** the workflow changes which extras it installs
- **THEN** the enforced set follows it, because the test reads the install line
  instead of restating the group names

#### Scenario: a major-only bound on a judging tool is rejected
- **WHEN** the test runner, type checker, or linter is bounded only at the major
  version
- **THEN** the governance test fails, because the failure this rule exists for
  was a minor-version bump

### Requirement: A dependency window restated outside project metadata SHALL be pinned to it

Any dependency window that must be restated outside `pyproject.toml` SHALL be
kept byte-identical to the declaration, enforced by a test.

The numpy and scipy windows are inlined in the CI workflow because qlib is
installed BEFORE the project and therefore cannot pick the constraint up from
project metadata. Restating is necessary; drifting apart silently is not — the
REGEN-2 determinism anchor reproduces only inside that window, so a workflow
that installs a different one would move the anchor without any spec changing.

#### Scenario: the workflow and project metadata name the same window
- **WHEN** the numpy or scipy constraint differs between the workflow and
  `pyproject.toml`
- **THEN** the governance test fails
