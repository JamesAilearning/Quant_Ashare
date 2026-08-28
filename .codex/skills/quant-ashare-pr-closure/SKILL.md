---
name: quant-ashare-pr-closure
description: "Prepare, publish, and close a Quant_Ashare pull request through local review, verification, Codex review requests, and focused follow-up fixes. Use when the user asks to push a PR, address review feedback, or drive a PR to a review-ready state."
---

# Quant_Ashare PR Closure

Drive one coherent pull request to a verified review-ready state without
expanding its scope or turning remote feedback into an endless loop.

## Before publishing

- Confirm the branch, base branch, intended PR scope, and working-tree state.
  Preserve unrelated user changes.
- Review the full diff locally using the repository's review guidance. Fix P0,
  P1, and P2 findings before push; record any accepted P3 explicitly.
- Run required targeted checks, import smoke for changed source modules, the
  repository-required full tests, lint/type checks, and OpenSpec validation
  when applicable. Run heavy checks serially, not in parallel.
- Inspect the staged diff before committing. The commit subject and body must
  describe the actual diff, including why any follow-up fix escaped an earlier
  review.

## Publish and review

- Push only the intended branch and open or update the matching PR.
- After every push that changes the PR, request a fresh `@codex` review when
  GitHub review access is available.
- Treat remote feedback as evidence to classify: new and actionable, already
  fixed, obsolete because the diff changed, or out of scope. Inspect the cited
  code and related producer/consumer paths before changing anything.
- For an actionable item, implement the smallest compliant fix, add or update
  a regression test, re-run the proportionate checks plus the required full
  suite, then repeat the publish-and-review step.

## Execute an authorized terminal action

- Merge or close the PR only when the user explicitly requests that exact
  action. A request to prepare, publish, review, fix, or monitor a PR does not
  authorize merging or closing it.
- Immediately before the mutation, resolve the exact repository and PR number,
  verify that the requested action still applies, and confirm that required
  checks and actionable review feedback do not block it. Stop and report any
  unmet requirement instead of bypassing it.
- After the authorized merge or close, verify the remote PR state and report
  the terminal state and head commit.

## Closing discipline

- Do not reply to GitHub comments or resolve remote review threads unless the
  user has explicitly authorized that action.
- Do not claim a PR is clean merely because an old thread remains visible;
  distinguish the current head's unresolved actionable feedback from historical
  discussion.
- Stop monitoring after the requested review cycle has no new actionable
  feedback, the PR has merged/closed, or further progress requires user or
  reviewer input. Summarize the current commit, validation, outstanding items,
  and any unexercised path.
