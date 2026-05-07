# Code Review Guide

Use this guide only when the user asks to review code, inspect a diff, review a PR, check uncommitted changes, audit a change, or identify potential issues.

Do not use the implementation guide during review unless the user explicitly asks to fix the findings.

## Goal

Provide a high-signal review that helps the user decide whether the change is safe to merge or needs follow-up.

The review should not become an implementation session.

## Review scope

Start from the most specific available input:

1. PR diff or changed files.
2. Uncommitted diff.
3. Files explicitly mentioned by the user.
4. Only then expand to surrounding code needed to verify a finding.

Avoid broad repo scans unless they are necessary to confirm a concrete risk.

## What to check

### 1. Correctness and behavior

- Logic errors.
- Broken control flow.
- Incorrect assumptions about qlib behavior.
- Edge cases and empty-state handling.
- Data shape mismatches.
- Silent fallbacks that hide failure.

### 2. Governance drift

Flag any change that:

- Introduces a second official metrics path.
- Treats experimental or research behavior as canonical.
- Moves research artifacts into production/runtime semantics.
- Changes canonical runtime behavior without an approved OpenSpec decision.
- Creates hidden coupling between contract validation and runtime selection.
- Blurs informational health/status with canonical/experimental meaning.

### 3. Layer-boundary drift

Check whether the change respects these boundaries:

- `src/core/`: canonical runtime contracts and approved runtime logic only.
- `src/data/`: data access and runtime-adjacent placeholders; no hidden selection semantics.
- `src/contracts/`: schemas, source-of-truth rules, provenance, validation boundaries, status fields.
- `web/`: operator-facing and informational views only.
- `research/` and `research/factor_lab/`: research-only, non-production, non-canonical.
- `tests/logic/`: runtime and placeholder behavior tests.
- `tests/governance/`: contract, boundary, and regression tests.

### 4. Tests and validation

Look for missing or weak tests around:

- Governance boundaries.
- Contract validation.
- Placeholder components that must remain intentionally unimplemented.
- Operator-visible status boundaries.
- Any behavior that could affect official metrics.

For OpenSpec-related changes, check whether `openspec validate` should be run.

### 5. Reliability and maintainability

Flag issues such as:

- Unhandled exceptions.
- Non-deterministic behavior in tests.
- Brittle path assumptions.
- Ambiguous naming that could create governance confusion.
- Excessive scope creep.

### 6. Security and safety

Flag issues such as:

- Secrets or credentials in code/logs.
- Unsafe file or shell operations.
- Overly broad permissions.
- User-controlled paths without validation.

## Severity labels

Use these labels consistently:

- `P0`: blocker; likely incorrect, unsafe, or violates core governance.
- `P1`: high; should be fixed before merge.
- `P2`: medium; should be fixed soon or clearly accepted.
- `P3`: low; optional improvement.

Do not inflate severity for style-only comments.

## Output format

Use this exact structure:

```md
## Review summary

一句话概括整体风险。不要夸张。

## Findings

### P1: <short title>
- Location: `<file>:<line>`
- Problem: ...
- Why it matters: ...
- Recommendation: ...

## Tests / validation gaps

- Ran: ...
- Not run: ...
- Missing coverage: ...

## Merge recommendation

Choose one:
- Safe to merge.
- Safe after addressing P1/P0 findings.
- Needs another pass after tests/validation.
```

If no findings are found:

```md
## Review summary

No blocking findings found in the reviewed scope.

## Findings

No issues found.

## Tests / validation gaps

- Ran: ...
- Not run: ...

## Merge recommendation

Safe to merge, assuming the unrun validations pass.
```

## Non-goals

Do not:

- Rewrite the implementation.
- Add new architecture unless the current change is unsafe.
- Suggest broad refactors unrelated to the diff.
- Apply agent-development rules unless the user asks for implementation.
- Start an OpenSpec loop unless the user explicitly asks for it.

## Suggested review prompts

Use one of these prompts when invoking Codex:

```text
Review the current diff using docs/codex/code-review.md. Do not modify files.
```

```text
Review this PR for governance drift, hidden fallback, missing tests, and canonical/experimental leakage. Use docs/codex/code-review.md. Do not fix anything yet.
```

```text
Review only the files changed in this branch. Use P0/P1/P2/P3 severity. If no issues are found, say so and list unrun validation.
```
