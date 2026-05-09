---
name: Agent task
about: Fully-specified task for an automated coding agent (OpenCode / Codex / Claude / etc.)
title: "[agent-task] <short imperative summary>"
labels: agent-task
---

<!--
This template exists because under-specified tickets cause agents to
guess. Fill in every section. If a section is genuinely N/A, write
"N/A — <one-line reason>" rather than deleting it.
-->

## Goal
<!-- One sentence. What outcome must exist after this task is done? -->

## Files to touch
<!--
List the files the agent is expected to modify, with the change scoped
to a function or section name. If the agent finds it must touch other
files (per `grep` of an affected symbol), it should report back rather
than silently widening scope.
-->
- `src/<path>` — modify `<function_or_class>`
- `tests/logic/<path>` — add `<TestClass>` covering `<behavior>`
- (do **not** touch other files unless required by `grep` results,
  in which case stop and report)

## Existing patterns to follow
<!--
Point at concrete examples in the same module so the agent matches
local convention rather than inventing a new one.
-->
- Error handling: see `src/core/<file>.py:<function>` — match its
  raise / log / fallback style.
- Convention reminders from AGENTS.md that apply here:
  - No silent fallback (return-on-error must raise or WARN+record).
  - Two-engines-one-schema if this touches Pipeline or
    WalkForwardEngine artifacts.
  - Every dict access must be cross-referenced to a producer (no
    invented field names).

## Acceptance criteria
<!-- Concrete checkable items. The agent ticks these. -->
- [ ] `pytest tests/logic/<file>.py -v` all pass
- [ ] `pytest tests/logic/ tests/governance/` no new failures
  (compare against `main` before the change)
- [ ] Commit message describes what the **diff** shows, not what was
  intended (per AGENTS.md > "Commit messages must match the diff,
  not the plan")
- [ ] Every dict / dataclass field read in the new code was found at
  a producer site via `grep`. No invented fields.
- [ ] If a contract was changed (signature / field / exception type
  / on-disk schema), every caller and every test referencing the
  old contract was updated in this PR.

## Out of scope
<!--
Anything the agent is explicitly NOT to do, so it does not bundle
unrelated cleanups into this PR.
-->
- (e.g. "do not refactor neighbouring functions even if they look
  similar", "do not migrate other engines to the same pattern in
  this PR")

## Reference
<!--
Optional: link reviews, prior PRs, design docs that motivate this
task. Keeps the agent from re-deriving context from scratch.
-->
- N/A
