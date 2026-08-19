## Context

Today Workbench is a read-only entry point that condenses daily-signal and job
artifacts for an operator. The detailed daily-decision page already treats a
version-marked artifact without valid metadata as corrupt, but the workbench
does not yet require the producer's exact supported schema version. Its job
summary also reaches the standard unified listing path, whose intentional
zombie reconciliation can persist a status update.

## Goals / Non-Goals

**Goals:**

- Fail closed before the workbench renders a legacy or unsupported
  daily-recommendation artifact as a current signal.
- Provide an explicitly named all-jobs reader that performs no job lifecycle
  reconciliation or disk writes.
- Preserve the Jobs page's existing zombie-reconciliation behavior.
- Pin the producer and UI schema-version literals without importing the qlib
  runtime into a Streamlit read-side helper.

**Non-Goals:**

- Do not change the daily-recommendation artifact schema or its producer
  semantics.
- Do not change job lifecycle ownership, stale-job policy, progress estimates,
  or the Jobs page.
- Do not change canonical runtime, training, backtesting, data selection, or
  official metrics.

## Decisions

### Reject every version other than the current producer version

The daily-recommendation writer will name its schema-version constant. The
workbench helper will carry a qlib-free copy of that value and reject a
missing, non-integer, boolean, or unequal version before it asks shared
metadata/provenance helpers to classify the signal. A small source-boundary
test pins the two values.

Importing the writer's constant directly was rejected: its module imports
canonical inference dependencies, while opening the workbench must not pull a
qlib runtime path into the Streamlit process. A duplicated, test-pinned literal
matches the existing UI treatment of qlib-bound production constants.

### Separate read-only aggregation from operational reconciliation

`job_io` will retain the normal, reconciling unified list used by the Jobs
page. It will add a dedicated `load_all_jobs_read_only` entry point that shares
filtering, sorting, normalisation, and pagination but asks the raw UI reader
not to reconcile zombies. Today Workbench will import only this dedicated
entry point.

Replacing reconciliation globally was rejected because the Jobs page owns the
operational lifecycle view and must repair a confirmed-dead running job.
Duplicating normalisation or pagination was rejected because it would create a
second interpretation of the job catalog.

## Risks / Trade-offs

- [The writer advances to a new schema version before the UI is updated] → the
  workbench displays `needs_verification`, the safe direction, until the
  matching reader support is deliberately added.
- [A dead process remains recorded as running on the workbench] → the
  workbench reports the recorded state without mutating it; the Jobs page is
  the explicit operational surface that reconciles and repairs the record.
- [Shared listing code regresses] → tests cover both the existing reconciling
  path and the new non-mutating path against a confirmed-dead job.
