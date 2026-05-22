## ADDED Requirements

### Requirement: Operator UI SHALL provide reusable common component primitives

The operator UI SHALL expose common presentation helpers for buttons, icon
buttons, badges, tags, cards, stat cards, tabs, accordions, modals, toasts,
tooltips, skeletons, empty states, error states, progress bars, spinners, form
field wrappers, and tables.

#### Scenario: common components are available to page-level UI work

- **WHEN** a page imports `web.operator_ui.components`
- **THEN** common component render helpers are importable
- **AND** each helper emits presentation-only markup or Streamlit display calls
- **AND** no helper reads runtime artifacts or computes official metrics

#### Scenario: common component styles are token-backed

- **WHEN** the centralized operator UI CSS is loaded
- **THEN** common component classes use design tokens for colors, spacing,
  radius, shadows, motion, and typography
- **AND** component states include focus-visible, disabled, loading, empty,
  error, and reduced-motion-safe styling where applicable

---

### Requirement: Operator UI SHALL expose browser presentation preferences through localStorage

The operator UI SHALL provide a browser-side presentation preference contract
for theme and color convention. The existing server-side preference file MAY
remain as a fallback, but browser localStorage SHALL be the first browser
source applied by the injected preference script.

#### Scenario: browser preferences are present

- **WHEN** the preference script runs and localStorage contains a supported
  theme or color convention
- **THEN** the document root receives `data-theme` and
  `data-color-convention` attributes from localStorage unless the saved
  server-side presentation preference changed since the previous injection
- **AND** legacy `data-qv2-theme` and `data-qv2-color-convention` attributes
  are also set for existing CSS compatibility

#### Scenario: sidebar preferences are changed

- **WHEN** the operator changes theme or color convention from the Streamlit
  sidebar and the server-side preference fallback changes
- **THEN** the preference script applies the changed server-side preference
  before reading stale localStorage values
- **AND** localStorage is synchronized to the changed presentation preference

#### Scenario: browser preferences are missing

- **WHEN** localStorage has no supported presentation preference
- **THEN** the script applies the server-provided fallback preference
- **AND** stores that presentation preference in localStorage
- **AND** no generated run configuration is changed

---

### Requirement: Operator UI SHALL showcase common components in the design-system page

The design-system demo page SHALL show common components and formatting
examples so future UI changes can visually inspect shared primitives.

#### Scenario: demo page is opened

- **WHEN** the operator opens the design-system page
- **THEN** token swatches, formatting examples, feedback states, controls,
  form fields, table markup, loading states, and overlays are represented
- **AND** the page remains QA/demo-only and does not compute official metrics
