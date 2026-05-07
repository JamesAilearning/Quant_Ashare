# OpenSpec Loop Guide

Use this guide only when the user explicitly asks to run one OpenSpec loop, propose/apply/archive an OpenSpec change, or validate OpenSpec governance.

## One-loop rule

When asked to run one OpenSpec loop:

1. Work on exactly one active change.
2. Use `/opsx:propose` only when no approved active change is ready for implementation.
3. Use `/opsx:apply` only when the current change is proposal-complete and validated.
4. After apply, always do a review checkpoint:
   - changed files
   - implemented scope
   - intentionally unimplemented items
   - tests run
   - `openspec validate`
   - scope drift / contract drift / governance drift check
5. Use `/opsx:archive` only when the change is complete, validated, tested, and still within approved scope.
6. Do not start the next change automatically unless explicitly asked.
7. Never work on more than one change in a single loop.

## Archive checklist

Before recommending archive, confirm:

- The scoped change is complete.
- Tests passed.
- `openspec validate` passed.
- Docs/tasks were updated.
- No runtime semantics changed beyond approved scope.
- No experimental or research behavior leaked into canonical behavior.
- No implicit fallback or hidden coupling was introduced.

## Proposal checklist

Before proposing a change, identify:

- Problem statement.
- Approved or requested scope.
- Non-goals.
- Affected contracts.
- Affected runtime behavior, if any.
- Governance risks.
- Validation plan.

## Apply checklist

Before applying an approved change:

- Confirm the approved proposal and tasks.
- Implement only approved scope.
- Keep runtime behavior unchanged unless the proposal explicitly approves it.
- Add or update tests for governance boundaries.
- Run targeted tests and `openspec validate`.

## Stop conditions

Stop and report rather than continuing if:

- More than one active change becomes involved.
- The implementation would exceed approved scope.
- Contract validation starts to affect runtime selection semantics.
- Experimental or research behavior is being promoted into canonical behavior.
- `openspec validate` fails and the fix is outside approved scope.
