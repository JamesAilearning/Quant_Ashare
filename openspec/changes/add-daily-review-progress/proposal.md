## Why

The daily decision page records append-only human decisions, but an operator
cannot quickly tell how many current candidates have a valid review, which
human labels are present, or whether a later correction changed a candidate's
current state.  The page needs a read-only progress view without implying that
any order or position changed.

## What Changes

- Add a pure daily-review projection from the selected artifact's candidate
  codes and the decision journal's existing effective view.
- Show a dated review summary and non-execution review labels on current
  candidate rows.
- Reuse that projection from Today Workbench's review queue rather than keep
  two independent review-counting rules.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `v2-daily-decision-page`: expose the selected artifact's human-review
  progress and current per-candidate review state.
- `v2-today-workbench`: consume the shared review projection for its existing
  navigation-only queue.

## Impact

- Affected UI: daily decision page and the existing Today Workbench queue.
- Affected inputs: selected read-only recommendation artifact and the
  web-owned decision journal.
- No backtest, training, serving, model selection, position, order, or
  trading-execution behavior changes.
