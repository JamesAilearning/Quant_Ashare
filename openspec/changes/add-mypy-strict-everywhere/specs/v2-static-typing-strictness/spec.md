## ADDED Requirements

### Requirement: Repo-wide mypy SHALL run in strict mode by default

`pyproject.toml`'s `[tool.mypy]` section SHALL set `strict = true`
once this change completes (PR 4). Strict mode SHALL apply to every
Python module under `src/`, `web/`, and `scripts/` except those
explicitly opted out via `[[tool.mypy.overrides]]` with each strict
flag set to `false`. The CI workflow SHALL invoke `mypy` on these
trees WITHOUT `continue-on-error`, so a strict-mode regression
fails the build rather than being logged as a warning.

The single permitted opt-out at completion is `src.factor_mining.*`
(parallel workstream). That opt-out's `[[tool.mypy.overrides]]`
block SHALL explicitly set **every flag implied by `mypy --strict`**
to `false` — at the time of writing, this means all sixteen of:
`warn_unused_configs`, `disallow_any_generics`,
`disallow_subclassing_any`, `disallow_untyped_calls`,
`disallow_untyped_defs`, `disallow_incomplete_defs`,
`check_untyped_defs`, `disallow_untyped_decorators`,
`no_implicit_optional`, `warn_redundant_casts`,
`warn_unused_ignores`, `warn_return_any`, `no_implicit_reexport`,
`strict_equality`, `strict_concatenate`, and `extra_checks`.
Future-proofing: the test in
`tests/logic/test_mypy_strict_default.py` SHALL read the strict-flag
set from mypy at runtime (or pin to a current mypy release) so the
opt-out's flag list cannot silently drift behind mypy's `--strict`
definition. (Codex P1 on PR #171 — listing only the FU-7 subset
would leave eight strict-implied flags active on factor_mining,
defeating the "single opt-out" claim.)

#### Scenario: default strict applies to all source trees

- **WHEN** `mypy src/ web/ scripts/` is invoked from the repo root
  with the project's `pyproject.toml`
- **THEN** mypy reports "Success: no issues found" on every file
  outside `src/factor_mining/`
- **AND** the exit code is 0

#### Scenario: factor_mining opt-out is the only override

- **WHEN** `pyproject.toml` is parsed
- **THEN** exactly one `[[tool.mypy.overrides]]` block exists
- **AND** its `module` list contains only `"src.factor_mining.*"`
- **AND** every flag implied by `mypy --strict` (16 at time of
  writing — see Requirement body) is explicitly set to `false` in
  that block

#### Scenario: CI fails on a strict regression

- **GIVEN** a PR introduces an untyped function in `src/core/`
  (covered by default strict)
- **WHEN** the CI workflow runs
- **THEN** the mypy step exits non-zero
- **AND** the step does NOT carry `continue-on-error: true`
- **AND** the PR cannot be merged until the regression is fixed

#### Scenario: opt-out cannot silently expand

- **GIVEN** a PR adds a new module pattern to the
  `[[tool.mypy.overrides]]` block (e.g. `"src.legacy.*"`) without
  an accompanying OpenSpec change
- **WHEN** the strict-default test suite runs
  (`tests/logic/test_mypy_strict_default.py`)
- **THEN** the test asserting `OPT_OUT_MODULES == ("src.factor_mining.*",)`
  fails
- **AND** the PR cannot be merged

### Requirement: Strict-mode migration SHALL ship as four sequential, independently revertable PRs

The strict-mode migration SHALL ship as four PRs, each scoped to a
directory and each carrying only additive `pyproject.toml` changes
until the final PR. PRs 1–3 SHALL extend the existing
`[[tool.mypy.overrides]]` whitelist (additive only). PR 4 SHALL
remove the whitelist and add the inverted `factor_mining` opt-out
(non-additive but mechanically reversible via `git revert`).

Each PR's CI step SHALL be scoped to the directories cleaned by
that PR (and earlier PRs), so a regression introduced in a later
PR cannot retroactively break the merged earlier PRs.

#### Scenario: PR 1 extends but does not remove whitelist

- **WHEN** PR 1 lands
- **THEN** `pyproject.toml`'s overrides whitelist contains the
  original FU-7 entries PLUS `"src.core.*"`, `"src.pit.*"`,
  `"scripts.*"`
- **AND** no override entry is removed

#### Scenario: PR 4 inverts whitelist to single opt-out

- **WHEN** PR 4 lands
- **THEN** `[tool.mypy] strict = true`
- **AND** the FU-7 whitelist entries are gone
- **AND** exactly one override block exists with
  `module = ["src.factor_mining.*"]`

#### Scenario: PR 2 revert restores PR 1 state cleanly

- **GIVEN** PRs 1 and 2 are both merged
- **WHEN** PR 2 is reverted via `git revert <merge-sha>`
- **THEN** the repository state matches the immediate-post-PR-1
  state — PR 1's annotations and override extensions remain
- **AND** the test suite passes
