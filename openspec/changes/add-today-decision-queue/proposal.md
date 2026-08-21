## Why

Today Workbench exposes useful summary cards, but its operational summary
selects one representative state.  When a data/signal problem and several
failed jobs coexist, the lower-priority problems disappear from the operator's
first view.  A read-only queue should instead make every visible blocker and
attention item actionable by navigation.

## What Changes

- Add a stable, read-only `今日待办` queue below the existing Workbench cards.
- Derive all visible items from existing data-health, update-status, daily
  signal, job-catalog, and decision-journal read models.
- Classify items as blocker, attention, in-progress, review, or information;
  preserve all distinct failures and provide an exact destination/context.
- Surface an explicit verification item for unreadable or incompatible inputs;
  never infer a healthy or executable state.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `v2-today-workbench`: add a complete, ordered, navigation-only daily queue
  without changing any runtime, serving, or trading semantics.

## Impact

- Affected UI: Today Workbench and one pure queue helper.
- Affected inputs: existing read-only operator artifacts and decision journal.
- No update, job, configuration, model-selection, serving, backtest, or
  trading-execution action is added to the Workbench.
