## Context

`web/operator_ui/pages/config_run.py` already has one authoritative
`config_dict` assembly path, canonical validators, preset loading, and a YAML
preview.  Its controls are currently arranged by implementation category,
which makes the normal research path and the rarely changed controls appear
equally primary.  The page launches research runs only; production serving is
configured elsewhere.

## Goals / Non-Goals

**Goals:**

- Make the everyday configuration path understandable in the order an
  operator makes decisions.
- Make every emitted field and its difference from the selected preset visible
  before launch.
- Keep the existing configuration builder, validation and `JobManager.start`
  call as the sole source of launch semantics.
- Make research-only status clear throughout the page.

**Non-Goals:**

- Change dataclass defaults, preset precedence, serialized keys, validation,
  metrics, or launch behavior.
- Add production-serving editing, deployment, automatic model selection, or
  background execution controls.

## Decisions

### Use progressive sections, not a multi-page wizard

The page will use a visible research goal/preset area followed by data scope,
strategy constraints, collapsed advanced settings, and a final review area.
This keeps prior inputs available for correction and avoids state transitions
that could alter widget or preset behavior.  A multi-page wizard was rejected
because it would add navigation state and make equivalence harder to prove.

### Derive review content from the emitted configuration

The final review will consume the existing `preview_config` that wraps the
single `config_dict` builder.  A pure helper will only group already-emitted
key/value pairs and identify changed preset fields; it will not create a
second configuration or supply defaults.  Rendering the form's individual
widget values again was rejected because it could drift from launch payloads.

### Make omission and unsupported historical fields explicit

When a prefilled configuration contains fields the page cannot safely edit,
the review will preserve them in the emitted YAML if the existing builder does
so; otherwise it will state they are not part of the supported emitted schema.
It will not silently claim a field was restored.  This matches the current
hard-fail unknown-key boundary rather than inventing fallback behavior.

### Keep launch controls in the review area

The existing validation verdict, duration estimate, and start/save controls
will be visually presented as the final review step.  They continue to use
the unchanged validators and `JobManager.start(config_dict, mode)` call.

## Risks / Trade-offs

- [Layout refactor can alter Streamlit widget state] → retain every existing
  widget key and construct `config_dict` once after all controls are read.
- [Preset comparison could hide a changed emitted key] → compare mappings by
  union of keys and mark fields absent from either side explicitly.
- [Long review content can crowd narrow screens] → use a vertically stacked,
  readable review with the existing YAML view; no critical status is hidden in
  a horizontal-only area.
- [Research may be mistaken for production] → retain the existing truthful
  preset wording and add a persistent research-only explanation near the
  launch action.

## Migration Plan

The change is a UI-only layout update.  Existing saved presets and prefilled
run configurations continue to flow through the same loader and validator.
Rollback is a single page/helper revert; no persisted artifact or runtime
migration is needed.

## Open Questions

None.  The work order fixes the scope to research-only configuration and
explicitly prohibits runtime-semantic changes.
