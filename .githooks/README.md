# Versioned git hooks

Git stores hooks in `.git/hooks/` by default, which is **per-clone and not version-controlled**. Pulling the repo does not bring the hooks with it.

This directory holds **versioned** hooks. To activate them on your local clone, run **once**:

```bash
git config core.hooksPath .githooks
```

Verify with:

```bash
git config --get core.hooksPath
# → .githooks
```

After that, the hooks in this directory run on every commit / push exactly as if they lived in `.git/hooks/`. Pulling new hook updates is automatic; you do not need to re-run the `config` command.

## What runs

### `pre-commit`

1. **AST syntax check** on every staged `.py` file. Catches the "claimed to move import to top but did not" regression class — the file fails to parse even though `pytest` passes on unrelated paths.

2. **Real import smoke** on every staged `src/` module (`src/core/foo.py` → `import src.core.foo`; a package `__init__.py` imports as the package itself). The AST parse alone cannot see an import-time `NameError` from a removed-but-still-referenced symbol, or a circular import — those only surface when the module is actually imported (hardening backlog #5). All changed modules run in ONE interpreter so the heavy-dependency startup cost (pandas, lightgbm) is paid once. Scope is `src/` only: `scripts/` are one-shot probes not designed for bare import, and tests already import under pytest.

3. **Targeted test run** on any matched `tests/logic/test_<basename>.py` for `src/core/<basename>.py` or `src/data/<basename>.py` files in the commit. Heavy E2E tests (gated by `RUN_E2E=1`) stay off; those require a qlib data bundle and run in CI only.

If any step fails the commit is rejected. Bypass with `--no-verify` only when the user explicitly asked you to.

## Why a versioned hook + manual activation

The two alternatives both have downsides:

* `.git/hooks/pre-commit` directly: not version-controlled, lost on every fresh clone.
* `pre-commit` framework (the Python package): adds a dependency, requires `.pre-commit-config.yaml` + a `pre-commit install` step, and surprises agents that read this repo cold.

The `.githooks/` + `core.hooksPath` approach is plain git, no extra dependency, and a single one-time `git config` per clone is easy to document.

## CI parallel

CI is configured separately at `.github/workflows/test.yml` and runs the same `pytest tests/logic/` plus `tests/governance/` on push. The pre-commit hook is not a substitute for CI — it is a fast local guard so an agent does not push a broken commit to the remote.
