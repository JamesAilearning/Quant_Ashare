# CLAUDE.md

Project-specific instructions for Claude / Claude Code working in this
repo. Personal preferences (e.g. "I pull, you eyeball, you sign off")
belong in user-level memory, not here.

## What this project is

A qlib-based A-share daily-frequency quantitative stock-picking system.
Trains models on Alpha158-style features, runs walk-forward backtests,
and exposes a Streamlit operator UI for configuration / job runs /
result inspection.

Active workstream: **automated factor mining** under
`src/factor_mining/` (GP search → IS/OOS validator → manual gated
promotion → qlib handler bridge). See `docs/factor_mining/` and
`openspec/changes/archive/` for the design context.

## Repository layout (top-level)

- `src/` — production code.
  - `factor_mining/` — GP factor mining (Phases 1-6). **D5 strict gate:
    must NOT import `qlib.*` or `src.pit.*` directly.** Reaches the PIT
    layer only through `src/factor_mining/pit_adapter.py`.
  - `core/` — orchestration: pipeline, walk-forward engine, model
    trainer, backtest runner, artifact serialization.
  - `data/` — PIT mechanics, feature dataset builder, qlib adapters.
  - `data_pipeline/` — bundle ingest, universe / benchmark / industry
    publishers.
  - `contracts/` — boundary validators (run artifacts, baseline smoke,
    feature dataset, prediction signal).
- `web/operator_ui/` — Streamlit UI (pages under `pages/`).
- `tests/` — split by layer: `governance/`, `logic/`, `data_pipeline/`,
  `pit/`, `regression/`.
- `openspec/` — spec-first change management. Proposals live in
  `openspec/changes/`; archived (= shipped) under
  `openspec/changes/archive/`.
- `config/` — YAML configs and presets.
- `docs/` — architecture and lessons.

## Spec-first workflow

Non-trivial changes follow the OpenSpec flow:

1. Propose a change spec under `openspec/changes/<name>/` (`/opsx:propose`).
2. Apply (`/opsx:apply`) — implement against the spec.
3. Archive (`/opsx:archive`) — moves to `openspec/changes/archive/`.

Use OpenSpec for: new modules, behavior-changing fixes, anything
crossing runtime boundaries. Skip for: pure refactors, test additions,
isolated bug fixes that don't change a documented contract.

## Tests

### Running

```sh
# Fast suite (default, CI-equivalent)
pytest

# Specific layer
pytest tests/logic/factor_mining/ -x --tb=short
```

### The E2E guard — **DO NOT** run E2E tests casually

E2E-marked tests (`@skip_unless_e2e` in `tests/e2e_guard.py`) hit real
qlib bundles, real model training, and real backtest pipelines. They
have frozen this user's machine in the past. They are gated by
`RUN_E2E=1`:

```sh
# CI default — skips E2E
pytest

# Run E2E (only after confirming you actually want this)
RUN_E2E=1 pytest tests/logic/test_backtest_runner.py
```

If you write a new test that needs more than a few seconds of real
compute or any real disk artifact, gate it with `@skip_unless_e2e` and
verify your default-config run still skips it.

### qlib bundle assumptions

Tests under `tests/logic/test_*qlib*.py` and similar should either:
- Use `pytest.importorskip("qlib")` + skip-if-no-bundle, or
- Mock at the right boundary (NOT `qlib.data.D` — that's an
  anti-pattern this project is moving away from; see PR7).

## D5 strict gate (factor mining)

`src/factor_mining/` modules must not `import qlib` or `import src.pit`.
The D5 gate is enforced by per-module
`test_*_does_not_import_qlib_or_pit*` tests. The PIT layer is reached
only via `src/factor_mining/pit_adapter.py` — that's the one place the
boundary is crossed.

When in doubt: if your factor-mining change wants qlib data, route the
data load through `pit_adapter` and pass dataframes into the evaluator.

## Pre-commit hooks

`pre-commit` runs a syntax check on staged Python files. Never use
`--no-verify` to skip; if a hook fails, fix the underlying issue.

## Branch + PR conventions

Observed naming:
- `factor-mining/<kebab>` — work on the factor-mining subsystem
- `feat(ui)`, `fix(ui)`, `ui/<kebab>` — operator UI
- `chore(...)`, `chore/<kebab>` — repo hygiene, OpenSpec archive, etc.
- `feat(...)`, `fix(...)`, `test(...)` — keep the conventional prefix
  in the commit title

For larger features, **stack PRs**: rebase the next branch on the
previous, note "Stacks on #N" in the body. Smaller fixes can branch
straight off `main`.

Worktrees are heavily used (see `git worktree list`). New work goes in
`D:/stock/Claude/qlib_trading_system_v2_<topic>` or under
`D:/stock/worktrees/`; clean up with `git worktree remove` when done.

## Configs

Presets live under `config/presets/`. The `default.yaml` is the
canonical starting point; `config_walk*.yaml` variants drive
walk-forward runs. **Don't hardcode local paths** in tracked configs —
use env-var substitution (`${QUANT_PROVIDER_URI}`, optionally with a
default: `${QUANT_PROVIDER_URI:-D:/qlib_data/my_cn_data_pit}`) so the
same config works across machines. The five operational env vars are
documented centrally in `docs/operations-env-vars.md`. Personal preset overrides should be named
`my_*.yaml` or `*.local.yaml` (gitignored).
