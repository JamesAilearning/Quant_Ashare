## ADDED Requirements

### Requirement: Successful daily-signal runs SHALL offer a date-specific review route

When the existing daily-recommend runner succeeds, the Run Center SHALL offer
an explicit action that opens the detailed daily-decision page at the date of
the published recommendation artifact. The date SHALL be derived only from a
published filename matching the established recommendation artifact pattern.
The Run Center SHALL NOT guess a date from stdout, wall-clock time, or the
newest file in the output directory.

#### Scenario: success publishes one dated recommendation artifact
- **WHEN** the runner succeeds and its published paths include exactly one
  dated daily-recommendation artifact
- **THEN** the page renders a direct action to view that date in the detailed
  daily-decision page
- **AND** the command result, including its entry-date disclosure, remains
  visible until the operator chooses that action

#### Scenario: success has no unambiguous published recommendation date
- **WHEN** the runner succeeds but its published paths contain no, or more
  than one, dated daily-recommendation artifact
- **THEN** the page gives only the existing generic review guidance
- **AND** it does not guess or preselect a recommendation date

#### Scenario: review action survives its separate Streamlit interaction
- **WHEN** a successful run publishes exactly one dated recommendation artifact
- **AND** the operator selects the rendered review action in a later Streamlit
  rerun
- **THEN** the Run Center SHALL retain that exact published date until the
  action is selected
- **AND** it SHALL consume the retained date after handing it to the detailed
  daily-decision page
- **AND** a later ambiguous result SHALL clear any older retained date
