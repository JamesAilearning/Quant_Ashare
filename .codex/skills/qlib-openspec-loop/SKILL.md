---
name: qlib-openspec-loop
description: Use when asked to run one OpenSpec loop, propose/apply/archive a change, validate OpenSpec governance, or perform scoped OpenSpec workflow in this qlib repo.
---

# qlib-openspec-loop

## Load order

1. Read the top-level `AGENTS.md`.
2. Read `docs/codex/openspec-loop.md`.
3. Read `docs/codex/agent-development.md` only if implementation is explicitly part of the current approved loop.

## Behavior

- Work on exactly one active change.
- Do not start the next change automatically.
- Do not widen approved scope.
- Always include the review checkpoint after apply.
- Run `openspec validate` when relevant.
- Stop and report conflicts instead of silently resolving them.
