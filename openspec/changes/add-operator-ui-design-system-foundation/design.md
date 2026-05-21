# Design: Operator UI Design System Foundation

## Decisions

### Streamlit-adapted tokens

The design token source is a single CSS file under
`web/operator_ui/static/theme.css`. It defines CSS variables and base utility
classes that Streamlit pages can reuse. The app injects this file once during
startup.

### Persisted appearance preferences

Streamlit does not provide direct first-paint localStorage control from
Python. For this local operator console, preferences are persisted to
`output/operator_ui/preferences.json` and applied at app startup. A small
client-side script sets document data attributes so CSS selectors can switch
theme and color convention.

### Formatting helpers

Formatting helpers remain pure Python functions in `web/operator_ui/formatting.py`
and have no Streamlit imports. They return stable strings for `None`, NaN,
infinite, and malformed inputs, preventing raw `nan`/`None` displays.

### Demo page

The demo page is a development/QA view exposed under a "System" navigation
section. It is informational only and does not access runtime artifacts.

## Risks

- Streamlit may apply CSS after initial render, so the implementation should
  minimize but cannot fully eliminate browser first-paint behavior.
- Later page PRs must adopt tokens and formatting helpers incrementally; this
  PR only creates the foundation.
