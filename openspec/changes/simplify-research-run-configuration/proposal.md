## Why

The research-run configuration page presents required settings and infrequently
used expert controls in one dense form.  That makes a normal research launch
hard to review, while also obscuring the boundary between an experimental run
and production serving.

## What Changes

- Reorganize the existing research configuration page into a progressive,
  review-first flow: goal and preset, data scope, strategy constraints,
  collapsed advanced settings, and a final read-only review.
- Add an explicit, non-production research boundary and replace ambiguous run
  wording with "Start research run".
- Add a human-readable full configuration review and preset-difference view
  without creating a second configuration builder.
- Preserve the emitted configuration, defaults, validation, preset precedence,
  and launch path for identical inputs.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `v2-operator-ui-console`: require the research configuration page to group
  controls progressively, expose a complete pre-launch review, preserve
  configuration equivalence, and state its non-production boundary.

## Impact

- Affected UI: `web/operator_ui/pages/config_run.py` and small read-only UI
  helpers, if needed.
- Affected tests: focused operator-UI source and helper tests.
- No APIs, runtime configuration contracts, metric calculations, production
  serving configuration, or job-launch semantics change.
