---
name: qlib-agent-development
description: Use when asked to implement, fix, refactor, add tests, update contracts, or make code changes in this qlib/OpenSpec repo. Do not use for pure code review.
---

# qlib-agent-development

## Load order

1. Read the top-level `AGENTS.md`.
2. Read `docs/codex/agent-development.md`.
3. If the task mentions OpenSpec, also read `docs/codex/openspec-loop.md`.

## Behavior

- Make the smallest compliant change.
- Preserve canonical qlib-native metric semantics.
- Keep experimental and research behavior explicitly labeled.
- Do not create hidden fallback, hidden coupling, or competing official paths.
- Add or update targeted tests when behavior or governance boundaries are affected.
- End with the completion summary from `docs/codex/agent-development.md`.
