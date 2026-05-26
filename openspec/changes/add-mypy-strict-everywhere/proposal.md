# Migrate Repo to `mypy --strict` by Default

## Why

PR #170 (FU-7) opted **5 high-stakes modules** into `mypy --strict`
via a `[[tool.mypy.overrides]]` *whitelist*; the repo-wide default
stays `strict = false`. That was the right scope for an audit
follow-up but leaves **202 strict-mode errors across ~33 files**
unenforced. As long as the global default is lenient, new code
keeps drifting (untyped defs, `Any` returns, unparameterised
generics) and the whitelist has to be extended each time a module
becomes "important enough" to lock down. The whitelist is a
maintenance tax.

End state: flip the default to `strict = true` and demote the
override block to a single **opt-out** for `src.factor_mining.*`
(parallel workstream — turning strict on there mid-flight would
generate constant merge noise; opt-out removed in a follow-up once
that workstream stabilises).

This is too much to ship in one PR. Four sequential PRs, each
covering one directory plus its share of `unused-ignore` /
trivial fixes, with the final PR carrying the protocol redesign +
the default flip.

### Real error inventory

Run: `mypy --strict --follow-imports=silent --explicit-package-bases
src/ web/ scripts/ --exclude src/factor_mining/`.

**By error code (202 total):**

| Code | Count | Typical fix |
|---|---|---|
| `type-arg` | 42 | Fill `dict[K, V]`, `tuple[T, ...]` annotations |
| `operator` | 36 | None-guard `date < None` / `float < None` comparisons |
| `unused-ignore` | 35 | Delete stale `# type: ignore` |
| `arg-type` | 28 | 15 in `src/contracts/` (protocol redesign), 13 misc |
| `no-untyped-def` | 27 | Add function annotations |
| `attr-defined` | 10 | Mostly POSIX-only `fcntl`/`os.killpg` on Windows path |
| `no-any-return` | 8 | Type external lib returns (json, yaml, etc.) |
| `union-attr` | 7 | `.get()` / `.x` on `Optional[T]` without guard |
| `assignment`, `no-untyped-call`, `var-annotated`, `return-value`, `dict-item` | 9 | Long tail |

**By directory (top files):**

| Directory | Errors | Worst file |
|---|---|---|
| `src/data/` | 64 | `feature_dataset_builder.py` (25) |
| `src/core/` | 53 | `walk_forward/engine.py` (15) |
| `web/operator_ui/` | 49 | `training_guards.py` (20) |
| `src/contracts/` | 15 | 5 each in `universe`/`taxonomy`/`benchmark` contracts |
| `scripts/` | 18 | `compare_factor_handlers.py` (6) |
| `src/pit/` | 3 | `query.py` (3× `unused-ignore`) |

## What Changes

Four sequential PRs. Each is independently mergeable, reviewable in
≤30 min, and ships its own CI strict-step that prevents regressions
*inside its scope*. The override block grows monotonically through
PRs 1–3, then inverts in PR 4.

### Batch 1 — Strict `src/core/* + src/pit/* + scripts/*`

The "annotations-heavy" batch. No surprises here — mostly
`no-untyped-def` + `type-arg` + `unused-ignore` deletions. The
heaviest single file is `walk_forward/engine.py` (15 errors, ~10
of them missing return-type annotations on private helpers).

Why bundled: `src/pit/` is 3 errors (all `unused-ignore`) and
`scripts/` is 18 errors across 8 files. Each would be a 2-line PR
on its own — bundling with `src/core/` keeps the batch count low
without making the diff hard to review (these files don't
cross-import).

pyproject: add `src.core.*`, `src.pit.*`, `scripts.*` to the
overrides whitelist. Existing entries (`src.core.walk_forward._resume`,
`src.core.regression_baseline`) become redundant but stay until
batch 4 cleanup.

CI: extend the FU-7 strict step from a file-list to a directory-list.

Expected post-batch error count: **~130**.

### Batch 2 — Strict `src/data/*`

The riskiest batch. `feature_dataset_builder.py` has 25 errors,
including 7 `operator` errors that are `date < None` /
`coverage_end_date - train_start` comparisons against
`Optional[date]` from the bundle manifest. These can't be fixed by
annotation alone — each one needs either an explicit
`if x is None: return …` guard or an `assert x is not None` that
matches the runtime contract.

**Behavioural risk:** if the current code silently no-ops when a
date is `None`, adding `assert` could turn a silent skip into an
`AssertionError`. The PR must preserve observable behaviour — if
in doubt, prefer `if x is None: return early` over `assert`.

Also: 8 unparameterised `dict` returns in `pit/delisted_registry.py`,
4+4 in `pit_validator.py`, and the tushare provider-bundle long tail.

pyproject: add `src.data.*` to overrides. Removes the three
existing strict-data entries (now redundant under the wildcard).

Expected post-batch error count: **~65**.

### Batch 3 — Strict `web/operator_ui/*`

Same operator-None-guard pattern as batch 2 but in UI code.
`training_guards.py` has 20 errors, mostly `date < train_start`
comparisons in `inspect_provider_metadata`'s caller graph (which
the bundle-health banner from PR #169 already consumes — be
careful not to regress the banner's "unconfigured" vs "error" vs
"warning" branches).

Platform-specific fixes:
- `fcntl.flock` calls in `job_io.py` / `job_manager.py` are POSIX-only.
  Wrap with `if sys.platform != "win32":` or move to a thin
  platform-shim module. **Verify current Windows behaviour first**
  — the user runs on Windows; whatever the current code does there
  is the floor we can't regress.
- `os.killpg` in `job_manager.py` is POSIX-only; same treatment.

pyproject: add `web.operator_ui.*` to overrides.

Expected post-batch error count: **~15** (all in `src/contracts/`).

### Batch 4 — Contracts protocol fix + flip default

Two pieces in one PR because they're naturally coupled — the
contracts fix removes the last 15 errors, and once the count is 0
the flip is mechanical.

**Contracts mismatch.** `UniverseArtifactProfile` /
`TaxonomyArtifactProfile` / `BenchmarkArtifactProfile` expose
their fields as `@property` (read-only). The validating protocols
in `src/contracts/_shared_validators.py` declare those same fields
as settable class attributes:

```python
class _HasPresenceFlags(Protocol):
    artifact_present: bool        # ← settable in Protocol = required
    manifest_present: bool
```

Strict mypy reads "settable attribute" as required and a read-only
`@property` as a narrower type — hence 5 `arg-type` errors per
contract file × 3 files = 15.

**Two viable fixes — needs ~30-min investigation in the PR itself:**

| Option | Change | Risk |
|---|---|---|
| **A** | Change protocols to use `@property` declarations | Public `ArtifactProfile` API stays stable; only affects internal protocols. **Lower risk if no caller sets these fields.** |
| **B** | Drop `@property` wrapping on the artifact profile fields | Breaks any caller that relies on the descriptor (computed field, validation hook). Higher blast radius. |

The PR's first task is to grep for setter usage (`profile.artifact_present = …`)
across the codebase. If grep is empty, option A. Otherwise option B
with a dedicated migration of each setter call site.

**The flip.** Once contracts is clean:

```toml
[tool.mypy]
strict = true                # was: false

[[tool.mypy.overrides]]
module = ["src.factor_mining.*"]
disallow_untyped_defs = false
disallow_untyped_calls = false
disallow_incomplete_defs = false
disallow_untyped_decorators = false
warn_return_any = false
warn_unreachable = false
no_implicit_optional = false
strict_equality = false
```

Removes the whitelist entries from PRs 1–3 (now redundant under
default-strict). Removes the dedicated FU-7 CI step (replaced by
the existing broad-mypy step, which loses `continue-on-error: true`
because strict is now the default — not a per-module aspiration).

Updates `tests/logic/test_mypy_strict_modules.py` (rename →
`test_mypy_strict_default.py`): the `STRICT_MODULES` whitelist
becomes `OPT_OUT_MODULES = ("src.factor_mining.*",)` and the
pyproject assertions invert.

Expected post-batch error count: **0**.

## Rollback

Each batch is structured to be **revertable independently**:

- **PRs 1–3:** the only pyproject change is *additive* — appending
  a module pattern to the overrides whitelist. Revert = remove the
  added line. The annotations / `unused-ignore` deletions stay
  (they're correct regardless of strict mode); only the
  enforcement step backs off.
- **PR 4:** revert is two steps — restore `strict = false`, restore
  the FU-7 whitelist block as it was after PR 3. The contracts
  protocol fix can stay independently (it's a correct fix
  regardless of mypy strictness). If the protocol fix itself
  causes downstream breakage, revert *that* commit specifically
  via `git revert <sha>` — the flip is a separate commit in the
  same PR.

A regression caught at PR N+1 doesn't require touching PRs 1..N.

## Non-Goals

- **No `src/factor_mining/` strict.** Parallel workstream. A
  follow-up PR after that stabilises (no live tickets for ~2 weeks)
  removes the opt-out — that work is NOT in this proposal.
- **No new tests for legacy behaviour.** This is a typing
  migration. If a strict fix would change observable behaviour
  (e.g. raising `AssertionError` where the old path silently
  returned), the fix must preserve behaviour even if "stricter" is
  tempting. Open a follow-up issue instead of bundling a
  behavioural change into a typing PR.
- **No refactoring "to make types fit."** If a function needs a
  union-type return or a class redesign to satisfy strict, defer
  that to a dedicated PR. Bundling typing with redesign defeats
  reviewability.
- **No qlib stubs.** `ignore_missing_imports = true` stays. Qlib
  stubs are an upstream concern (and would drift if vendored here).
- **No CI matrix expansion.** The strict step runs on
  `ubuntu-latest` + `python 3.11` only; strict mode is not
  platform-sensitive (`platform` branches are runtime).
- **No `# type: ignore` whack-a-mole.** `unused-ignore` errors are
  fixed by *deleting* the stale comment. If a new ignore is
  genuinely needed, it carries `# type: ignore[error-code]  # reason: …`
  with the error code and a one-line justification.
- **Not framed as a behavioural-spec change.** No new capability,
  no requirement deltas — this is a tooling / hygiene migration.
  We're still using the OpenSpec change folder for traceability
  (and to match the project's established cadence), but there are
  no `specs/` deltas to ship.

## Open Questions

1. **POSIX-only call behaviour on Windows (PR 3).** What does
   `fcntl.flock` *currently* do on the user's Windows machine — is
   it `ImportError` at module load? Caught by a `try/except` we
   haven't found yet? The PR can't add the `sys.platform` guard
   until this is verified, otherwise we might *introduce* a
   regression instead of just typing the existing code.
2. **Contracts option A vs B (PR 4).** Decision deferred to the PR
   itself after a grep pass — but if the user already knows
   whether the artifact profiles' fields are externally set
   anywhere, that knowledge would short-circuit ~30 min of
   investigation.
3. **CI `continue-on-error` flip timing (PR 4).** Drop the flag in
   the same PR as the strict default flip (cleanest), or in a
   follow-up after one CI cycle of monitoring (safer)? Recommended:
   same PR — the strict step in PRs 1–3 already proves the
   per-directory scope is clean.
