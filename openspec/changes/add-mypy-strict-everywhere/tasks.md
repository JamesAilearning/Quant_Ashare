# Tasks: Migrate Repo to `mypy --strict` by Default

Four sequential PRs. Each PR ships its own CI strict-step that
prevents regressions in its scope. Error counts in parentheses are
the expected `mypy --strict --follow-imports=silent` total *after*
that PR lands.

## OpenSpec (propose stage)

- [x] Draft `proposal.md` (Why / What Changes / Rollback / Non-Goals
      / Open Questions)
- [x] Draft `tasks.md` (this file)
- [ ] No spec deltas — tooling migration, no capability changes

## PR 1 — Strict `src/core/*` + `src/pit/*` + `scripts/*` (target: 202 → ~130)

Annotations-heavy. Mostly mechanical.

### Code changes — `src/core/` (53 errors)

- [ ] `src/core/walk_forward/engine.py` (15 errors):
      - Annotate the ~10 `no-untyped-def` private helpers — return
        types first, then param types from call sites
      - Fix the 1 `return-value` tuple-type mismatch (likely
        related to #163 timing refactor leaving a 3-tuple where
        a 4-tuple is now returned)
- [ ] `src/core/pipeline_result_artifacts.py` (9): mostly
      `type-arg` fills; one `var-annotated`
- [ ] `src/core/backtest_runner.py` (9): 4× `type-arg` + 4×
      `unused-ignore` + 1× `return-value` annotation
- [ ] `src/core/model_trainer.py` (7): `no-untyped-def` sweep
- [ ] `src/core/pipeline.py` (6): `type-arg` + `no-untyped-def`
- [ ] `src/core/qlib_runtime.py` (3× `unused-ignore`): delete
- [ ] `src/core/walk_forward/aggregate.py` (2): annotate
- [ ] `src/core/model_config_projection.py`, `signal_analyzer.py`:
      remove `unused-ignore`

### Code changes — `src/pit/` (3 errors)

- [ ] `src/pit/query.py`: remove 3× `unused-ignore`

### Code changes — `scripts/` (18 errors)

- [ ] `scripts/compare_factor_handlers.py` (6): `no-any-return`
      on JSON helpers; fill `dict[str, Any]`
- [ ] `scripts/run_walk_forward.py` (3): `dict[str, Any]` annotation
      + 2 untyped helpers
- [ ] `scripts/data_quality/verify_survivorship.py` (3): annotate
- [ ] `scripts/ingest_tushare_industry.py` (2): annotate
- [ ] `scripts/compare_walk_forward_runs.py` (2): annotate
- [ ] `scripts/tushare_preflight.py` (1): single annotation
- [ ] `scripts/diagnose_fold.py` (1): `no-any-return` fix

### pyproject + CI + test

- [ ] `pyproject.toml`: add `"src.core.*"`, `"src.pit.*"`,
      `"scripts.*"` to the `[[tool.mypy.overrides]]` `module` list
- [ ] `.github/workflows/test.yml`: replace the file-list in the
      "Type check strict modules (audit FU-7)" step with directory
      arguments:
      ```
      mypy --follow-imports=silent --explicit-package-bases \
        src/core/ src/pit/ scripts/ \
        src/data/_segment_embargo.py \
        src/data/bundle_manifest.py \
        src/data/_feature_dataset_cache.py
      ```
      (the three data files keep their explicit listing until PR 2
      extends the wildcard)
- [ ] `tests/logic/test_mypy_strict_modules.py`: extend
      `STRICT_MODULES` to include wildcard patterns
      (`"src.core.*"`, `"src.pit.*"`, `"scripts.*"`). May need a
      new `STRICT_MODULE_PATTERNS` constant alongside
      `STRICT_MODULES` so the substring check in the test still
      works.

### Validation

- [ ] `mypy --follow-imports=silent --explicit-package-bases
      src/core/ src/pit/ scripts/` → "Success: no issues found"
- [ ] `pytest tests/logic/` green (full local run, not just the
      strict-mypy test file)
- [ ] `ruff check src/core/ src/pit/ scripts/` clean
- [ ] CI matrix green on the PR (6 jobs × ubuntu/windows × py 3.10/3.11/3.12)

## PR 2 — Strict `src/data/*` (target: ~130 → ~65)

The behavioural-risk batch. Each None-guard must preserve current
runtime behaviour.

### Code changes (64 errors)

- [ ] `src/data/feature_dataset_builder.py` (25 errors):
      - **First**: read `tests/logic/test_feature_dataset_builder.py`
        + any tests that fixture a `None` `coverage_end_date`. The
        guard pattern must match what the tests assert.
      - 7× `operator` — `date < None` / `coverage_end_date -
        train_start`. For each, pick `if coverage_end_date is None:
        return …` (early return preserves old "silent skip"
        behaviour) OR `assert coverage_end_date is not None`
        (matches the manifest-contract guarantee). Document the
        choice in a one-line code comment.
      - 3× `unused-ignore`: delete
      - 3× `type-arg`: fill `dict[K, V]`
      - Remaining ~12: long tail per per-file mypy run during PR
- [ ] `src/data/pit/pit_validator.py` (9): 4× `unused-ignore` + 4×
      `type-arg` + 1× `no-untyped-def`
- [ ] `src/data/pit/delisted_registry.py` (8): all 8 are `type-arg`
      — fill `dict[str, X]`
- [ ] `src/data/pit/index_membership.py` (6): per-file audit
- [ ] `src/data/tushare/provider_bundle/publisher.py` (4): mixed
- [ ] `src/data/taxonomy_artifact_publisher.py` (3): mixed
- [ ] `src/data/trading_calendar.py` (2× `unused-ignore`): delete
- [ ] `src/data/universe_artifact_publisher.py` (2): mixed
- [ ] `src/data/tushare/provider_bundle/_utils.py` (2): mixed
- [ ] `src/data/benchmark_artifact_publisher.py` (1× `unused-ignore`):
      delete
- [ ] `src/data/tushare/client.py` (1× `unused-ignore`): delete

### pyproject + CI + test

- [ ] `pyproject.toml`: replace the three explicit data entries
      (`src.data._segment_embargo`, `src.data.bundle_manifest`,
      `src.data._feature_dataset_cache`) with `"src.data.*"` in the
      overrides list
- [ ] `.github/workflows/test.yml`: drop the three explicit data
      files from the strict-mypy step args, add `src/data/`
- [ ] `tests/logic/test_mypy_strict_modules.py`: remove the three
      explicit data entries from `STRICT_MODULES`; add
      `"src.data.*"` to the patterns list

### Validation

- [ ] `mypy --follow-imports=silent --explicit-package-bases
      src/data/` → "Success"
- [ ] `pytest tests/logic/` — full regression. Pay extra attention
      to `test_feature_dataset_cache.py`,
      `test_segment_embargo.py`, `test_feature_dataset_builder.py`
- [ ] Manually inspect each `operator` fix's git diff to confirm
      the early-return vs assert choice matches the test fixtures

## PR 3 — Strict `web/operator_ui/*` (target: ~65 → ~15)

Mirror of PR 2's None-guard pattern, plus the POSIX-platform fix.

### Pre-work (verify, don't change)

- [ ] **Open Question #1 from proposal:** what does the current
      `fcntl.flock` reference *do* on Windows? Read `job_io.py` +
      `job_manager.py`, run the operator UI on the user's machine,
      confirm whether `fcntl` import is currently inside a
      `try/except ImportError` block or whether the file's import
      side-effects are dead-code on Windows. This determines
      whether the fix is "add a guard" or "the guard already
      exists, just tell mypy". DO NOT add platform branches
      blindly.

### Code changes (49 errors)

- [ ] `web/operator_ui/training_guards.py` (20 errors):
      - 15× `operator` None-guard sweep — same pattern as PR 2's
        `feature_dataset_builder`. The caller graph for these is
        the bundle-health banner from PR #169; **the banner's 4
        states (ok / warning / error / unconfigured) must stay
        unchanged**. Re-run `tests/logic/test_bundle_health_banner.py`
        after each guard added.
      - Remaining 5: per-file audit
- [ ] `web/operator_ui/pages/config_run.py` (8): mixed
- [ ] `web/operator_ui/pages/walk_forward.py` (6): mixed
- [ ] `web/operator_ui/job_io.py` (6): `fcntl.flock` `attr-defined`
      + misc
- [ ] `web/operator_ui/report_reader.py` (3): mixed
- [ ] `web/operator_ui/pages/results.py` (3): mixed
- [ ] `web/operator_ui/job_manager.py` (2): `os.killpg`
      `attr-defined` + misc

### pyproject + CI + test

- [ ] `pyproject.toml`: add `"web.operator_ui.*"` to overrides
- [ ] `.github/workflows/test.yml`: add `web/operator_ui/` to the
      strict-mypy step args
- [ ] `tests/logic/test_mypy_strict_modules.py`: add
      `"web.operator_ui.*"` to patterns

### Validation

- [ ] `mypy --follow-imports=silent --explicit-package-bases
      web/operator_ui/` → "Success"
- [ ] `pytest tests/logic/test_bundle_health_banner.py
      tests/logic/test_training_guards.py …` (every UI test file)
- [ ] **Manual UI smoke test** on Windows:
      `streamlit run web/operator_ui/streamlit_app.py` — visit
      Jobs / Results / Walk-Forward pages, confirm no
      `ImportError` from platform-guards and bundle-health banner
      renders correctly across the 4 states

## PR 4 — Contracts protocol fix + flip default (target: ~15 → 0)

Two commits in one PR: protocol fix (commit 1), pyproject flip
(commit 2). Separable for `git revert`.

### Step 1 — Decide contracts approach (Open Question #2)

- [ ] `grep -rn 'profile\.\(artifact_present\|manifest_present\|metadata\|stale_days\|coverage_ratio\|snapshot_end\|has_snapshot_at_mismatch\) =' src/ tests/ web/ scripts/`
      — find every setter call
- [ ] **If grep is empty → Option A** (change protocols to
      `@property` declarations). Smaller diff, preserves public
      API.
- [ ] **If grep finds setters → Option B** (drop `@property` on
      the artifact profile fields). Larger diff; verify each setter
      call site does what the original `@property` getter computed.
- [ ] Document the choice in the PR description with the grep
      output as evidence.

### Step 2 — Apply contracts fix (15 errors)

**If Option A** (likely):

- [ ] `src/contracts/_shared_validators.py`: change each protocol
      class to use `@property` declarations:
      ```python
      class _HasPresenceFlags(Protocol):
          @property
          def artifact_present(self) -> bool: ...
          @property
          def manifest_present(self) -> bool: ...
      ```
      For all 6 protocols (`_HasPresenceFlags`, `_HasMetadata`,
      `_HasStaleness`, `_HasCoverage`, `_HasSnapshotEnd`,
      `_HasSnapshotAtMismatch`).

**If Option B:**

- [ ] `src/contracts/universe_data_contract.py`,
      `taxonomy_data_contract.py`, `benchmark_data_contract.py`:
      drop `@property` on the affected fields; convert to dataclass
      fields or computed-once-then-cached if the property body did
      work.
- [ ] Update each setter call site found in Step 1.

Either option:

- [ ] `pytest tests/logic/test_universe_data_contract.py
      tests/logic/test_taxonomy_data_contract.py
      tests/logic/test_benchmark_data_contract.py …` — every
      contracts test green.
- [ ] **Commit 1** of the PR with title: `fix(contracts): align
      Artifact protocols with @property fields`

### Step 3 — Flip pyproject to default-strict

- [ ] `pyproject.toml`:
      - Set `strict = true` under `[tool.mypy]`
      - Remove the existing `[[tool.mypy.overrides]]` whitelist
        block entirely (every module covered by default-strict)
      - Add new `[[tool.mypy.overrides]]` block with **every flag
        implied by `mypy --strict`** set to `false` (16 flags at
        time of writing — see proposal.md's "The flip" block for
        the canonical list). Codex P1 on PR #171: an incomplete
        opt-out would leave several strict-implied flags active on
        factor_mining and defeat the "single opt-out" claim.

### Step 4 — CI flip

- [ ] `.github/workflows/test.yml`:
      - Remove the dedicated "Type check strict modules (audit
        FU-7)" step entirely
      - Update the existing broad `mypy src/ web/ scripts/` step:
        drop `continue-on-error: true` — strict is now the default,
        no per-module aspiration left
      - Verify the broad step's args still exclude
        `src/factor_mining/` (or let the pyproject override do
        the work)

### Step 5 — Test invert

- [ ] `tests/logic/test_mypy_strict_modules.py` → rename to
      `test_mypy_strict_default.py`:
      - Replace `STRICT_MODULES` whitelist assertion with
        `OPT_OUT_MODULES = ("src.factor_mining.*",)` blacklist
        assertion
      - New test: `[tool.mypy] strict = true` is set in pyproject
      - New test: the opt-out block has **every flag implied by
        `mypy --strict`** set to `false`. Cross-check the flag list
        at runtime against `mypy.main.strict_flag_assignments` (or
        whatever the public API is in the pinned mypy version) so
        the test fails loudly when mypy adds a new strict-implied
        flag. Mirror of FU-7's "strict flags can't be silently
        diluted" but inverted. (Codex P1 on PR #171.)
      - Remove the "CI invokes strict check" test (CI now runs the
        broad mypy step with strict-default; verify that step
        exists instead)

### Validation

- [ ] `mypy --strict src/ web/ scripts/` → "Success: no issues
      found" (factor_mining excluded via the opt-out)
- [ ] `pytest tests/logic/` green
- [ ] CI on the draft PR — confirm the broad mypy step passes
      without `continue-on-error`
- [ ] **Commit 2** of the PR with title: `chore(mypy): flip default
      to strict (mypy-strict batch 4/4)`

## Cross-Batch Hygiene

- [ ] No pre-commit hooks bypassed (`--no-verify` forbidden)
- [ ] No `RUN_E2E=1` invocations (E2E tests freeze the user's
      machine — memory `feedback_e2e_tests.md`)
- [ ] No changes to `src/factor_mining/` in any of the 4 PRs
- [ ] Each PR's CI strict step is scoped to the directories it
      cleaned — a regression elsewhere doesn't block the PR
- [ ] Each PR has a one-line entry in the project changelog (if
      one exists) referencing this OpenSpec change ID

## Deferred (NOT this proposal)

- **Remove the `src.factor_mining.*` opt-out.** Separate PR after
  the factor-mining workstream stabilises (no live tickets ~2
  weeks). Same shape as PRs 1–3.
- **Qlib type stubs.** `ignore_missing_imports = true` stays.
- **Ruff annotation rules.** Aligning ruff's `ANN*` rules with
  mypy strict is a separate design discussion.
- **`# type: ignore` discipline as a CI rule.** The proposal's
  Non-Goals says new ignores must carry `[error-code]  # reason`,
  but enforcing this via a ruff rule or pre-commit hook is out
  of scope here.
