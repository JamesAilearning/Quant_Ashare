---
name: qlib-code-review
description: Use when asked to review code, inspect a PR, review a diff, check uncommitted changes, audit a change, or identify risks in this qlib/OpenSpec repo. Do not use for implementing features unless the user explicitly asks to fix findings.
---

# qlib-code-review

## Load order

1. Read the top-level `AGENTS.md`.
2. Read `docs/codex/code-review.md`.
3. Do not read or apply `docs/codex/agent-development.md` unless the user explicitly asks to fix findings.

## Behavior

- Do not modify files.
- Review the diff or changed files first.
- Prioritize governance drift, hidden fallback, canonical/experimental leakage, missing tests, and correctness risks.
- Use `P0`, `P1`, `P2`, `P3` severity labels.
- Include exact file/line references when available.
- If no findings are found, say so and list unrun validations.

## Output

Follow the output format in `docs/codex/code-review.md` exactly.
