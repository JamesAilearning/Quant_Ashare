# Design: UI Training Date Guards

## Boundary

The guard runs entirely in `web/operator_ui`. It reads local provider metadata
and files that already exist on disk:

- provider-adjacent `manifest.json`;
- provider-adjacent `validation.json`;
- `calendars/day.txt`;
- `instruments/*.txt`.

It does not initialize qlib, invoke Tushare, or import core runtime engines.

## Validation

Pipeline runs receive hard errors for:

- invalid ISO date strings;
- non-strict train/valid/test ordering;
- date ranges outside known provider coverage;
- `test_end` on or after the provider's final trading day;
- missing named instrument universe files.

Warnings are used for non-blocking operator context, such as fewer than twenty
provider trading days after `test_end`, because signal forward-return summaries
may be incomplete near the tail.

## UI

Training controls are rendered as normal Streamlit widgets rather than a
single form so changing dates or provider paths rerenders validation state and
updates the Run button immediately.
