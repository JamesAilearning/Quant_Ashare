## ADDED Requirements

### Requirement: Operator UI SHALL provide centralized design tokens

The operator UI SHALL define reusable visual tokens in a single CSS source
that can be injected by the Streamlit app without changing runtime logic.

#### Scenario: app startup injects design tokens

- **WHEN** the operator UI app starts
- **THEN** it loads the centralized token CSS file
- **AND** applies the tokens to the Streamlit page shell
- **AND** does not read or mutate runtime result artifacts

#### Scenario: theme and color convention tokens are available

- **WHEN** the CSS tokens are loaded
- **THEN** light, dark, and auto theme selectors are available
- **AND** Chinese and western positive/negative color conventions are available
- **AND** numeric displays can opt into tabular-number styling

---

### Requirement: Operator UI SHALL persist presentation preferences separately from runtime configuration

Theme and color-convention preferences SHALL be operator UI presentation
settings only. They SHALL NOT be written into pipeline or walk-forward run
configuration files.

#### Scenario: preferences are changed

- **WHEN** the operator changes theme or color convention from the UI shell
- **THEN** the preference is persisted under `output/operator_ui`
- **AND** the app reapplies it on the next startup
- **AND** no generated run `config.yaml` is changed

---

### Requirement: Operator UI SHALL format missing and non-finite display values consistently

Shared formatting helpers SHALL return stable display strings for missing,
NaN, infinite, and malformed values. Pages SHALL NOT expose raw `None`,
`nan`, or `inf` strings through these helpers.

#### Scenario: numeric value is missing or non-finite

- **WHEN** a formatting helper receives `None`, NaN, infinity, or a malformed value
- **THEN** it returns the shared unavailable display string
- **AND** it does not raise

#### Scenario: valid values are formatted

- **WHEN** a formatting helper receives a valid percentage, number, money,
  duration, relative time, or absolute date value
- **THEN** it returns a human-readable display string
- **AND** the helper performs display formatting only, not metric computation

---

### Requirement: Operator UI SHALL expose a design-system demo page

The operator UI SHALL provide a development/QA page that displays the current
tokens and formatting examples.

#### Scenario: demo page is opened

- **WHEN** the operator opens the design-system demo page
- **THEN** token swatches, typography examples, and formatting examples are visible
- **AND** no official runtime metrics are computed
