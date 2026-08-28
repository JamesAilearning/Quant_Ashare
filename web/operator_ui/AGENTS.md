# Operator UI rules

`web/operator_ui/` is an operator-facing consumer of explicit runtime and
artifact contracts. It is not a second runtime, metrics, selection, promotion,
or data-repair path.

## Before changing a page

- Read the page, its sibling helpers, and the producer of every field to be
  rendered. Never guess report or JSON schema fields.
- Keep parsing and classification in Streamlit-free helpers when it needs
  regression coverage; keep session state and query-parameter writes at the
  page boundary.
- Reuse existing audited runners, job controllers, and navigation helpers. Do
  not recreate process, lock, stop, or status behavior in a page.

## Operator-visible truthfulness

- Render missing, corrupt, foreign, stale, superseded, and unverifiable
  artifacts as explicit states. Never replace them with a default, a current
  artifact, or a nearby run.
- Do not recalculate official metrics, infer trading permission, promote a
  research result, or turn operator annotations into canonical runtime input.
- A button that launches work must name the existing runner it invokes and make
  clear whether launch, completion, and success are distinct states.

## Verification

- Cover changed normal, empty, failure/corruption, and running/disabled states
  where applicable with focused helper and page/AppTest coverage.
- For material visual or navigation work, perform a browser acceptance pass and
  report any unavailable real-artifact path rather than claiming it was tested.
- Run the root-required test, lint, import-smoke, OpenSpec, and local-review
  gates; do not run heavy tests concurrently.
