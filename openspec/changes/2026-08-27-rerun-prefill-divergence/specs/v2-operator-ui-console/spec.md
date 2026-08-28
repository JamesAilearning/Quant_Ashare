# Delta for v2-operator-ui-console

## ADDED Requirements

### Requirement: A rerun prefill SHALL actually apply, and SHALL say what it overwrote

Choosing "rerun with this configuration" SHALL make the source run's
configuration authoritative for every field the page can submit, overwriting
whatever the current session holds. Conditional application (write only while
the field's session key is unset) is FORBIDDEN: the page seeds every `cr_*`
key on its first render, so the condition is false on the most common path and
the prefill applies to nothing while the banner claims it applied.

Each source payload SHALL be applied at most once PER REQUESTING ACTION, so
edits made AFTER the prefill are not undone by later reruns of the Streamlit
script, while an operator who explicitly asks for the same run again gets that
run's values again.

Identity SHALL therefore be minted where the action happens, not derived from
the payload alone. Deriving it from the source run and its archived config
makes a second, explicit request for the SAME run indistinguishable from an
ordinary rerender: the application is skipped, the operator's intervening edits
survive, and the banner nonetheless states that the source run overwrote the
fields — so the launched experiment differs from the run the operator
deliberately reselected.

The overwrite SHALL NOT be silent. Every field whose prior session value
differed from the prefilled value SHALL be listed with both values, so an
operator who had been editing can see exactly what the rerun replaced.

Fields outside the page's submit schema SHALL NOT be written into session
state under a `cr_*` key: those keys collide with widget keys.

Every field the prefill writes SHALL be read back by the widget that submits
it, including the walk-forward window endpoints. A field written into session
state but never read is indistinguishable from one that was never prefilled:
the launched run silently covers a different window than the source run, and
the pre-launch review cannot see it either (both sides then show the widget's
own live default).

Reading a prefilled value SHALL NOT seed a value into session state when no
prefill is present. Seeding a provider-derived default freezes a first-render
no-calendar fallback and ignores the window recomputed from the current
provider calendar on later reruns.

#### Scenario: prefill overwrites a field the operator had already set

- **GIVEN** an operator who opened the config page (seeding every field key),
  set `topk` to 30, and then chose "rerun with this configuration" from a run
  whose `topk` is 50
- **WHEN** the config page renders
- **THEN** `topk` is 50, and the page lists `topk` as overwritten from 30 to 50

#### Scenario: an edit made after the prefill survives

- **GIVEN** a rerun prefill that has already been applied in this session
- **WHEN** the operator then edits a field and the script reruns
- **THEN** the operator's later edit stands; the prefill is not re-applied

#### Scenario: the walk-forward window is restored from the source run

- **GIVEN** a rerun prefill from a walk-forward run whose overall window
  differs from this machine's calendar-derived default
- **WHEN** the config page renders the window fields
- **THEN** they show the source run's window

#### Scenario: no prefill leaves the window on the live calendar default

- **GIVEN** no rerun prefill in this session
- **WHEN** the config page renders the walk-forward window fields
- **THEN** they show the default recomputed from the current provider
  calendar, and nothing is written into session state for them

### Requirement: A failed prefill SHALL be reported, never silently empty

The config page SHALL report, in the page body, why a rerun prefill produced
no fields. Returning an empty configuration while the page still shows a
"prefilled from run X" banner is FORBIDDEN — the operator cannot tell a failed
parse from a run that happened to carry no settings.

Reportable failures SHALL include at minimum: the archived `config.yaml` is
not parseable YAML, and its top level is not a mapping.

The producing page SHALL decode the archived `config.yaml` as strict UTF-8.
Lossy decoding is FORBIDDEN: replacement characters can yield a document that
parses successfully with a silently rewritten value. On a decode failure the
producing page SHALL report it in place and SHALL NOT navigate to the config
page, so a corrupted payload never becomes a prefill.

#### Scenario: unparseable archived configuration

- **GIVEN** a run whose `config.yaml` is not valid YAML
- **WHEN** the operator chooses "rerun with this configuration"
- **THEN** the config page states that nothing was prefilled and why

#### Scenario: archived configuration is not valid UTF-8

- **GIVEN** a run whose `config.yaml` contains bytes that are not valid UTF-8
- **WHEN** the operator chooses "rerun with this configuration"
- **THEN** the results page reports the decode failure and stays on the results
  page

### Requirement: The pre-launch review SHALL classify divergence from the source run

The config page SHALL compare the configuration it is about to submit against
the run it prefilled from, and SHALL disclose the divergences BEFORE launch, so
a run can never be attributed to a configuration it did not use. A prefill that
applied correctly still diverges once the operator edits anything afterwards.

The disclosure SHALL separate the following classes, because each calls for a
different operator action, and burying a real value change under schema-drift
noise teaches operators to ignore the whole block:

- a field present on both sides with a different value;
- a field the source run did not record (its schema was narrower);
- a field belonging to the other run mode, which this launch will not submit;
- a field that is scoped to a single run (`output_dir`, injected by the job
  manager) and therefore neither carried nor missing.

"Belongs to the other run mode" SHALL be decided from the other mode's own
schema, never from mere absence from the current mode's schema. A key absent
from BOTH schemas is a removed legacy field; classifying it as other-mode
while the unsupported-field report simultaneously calls it unsupported hands
the operator two contradictory claims about the same key. Such keys SHALL be
left to the unsupported-field report alone.

The run mode SHALL itself be a compared field. For UI-launched runs the mode
is recorded in the job ledger rather than the archived `config.yaml`, so the
comparison baseline SHALL incorporate the separately carried source mode;
otherwise switching a rerun from one mode to the other is reported as
field-for-field identical. Where the archived configuration records a mode of
its own, that value SHALL win — it is the run's own record rather than the
ledger's restatement. An absent source mode SHALL NOT be invented.

For a field the source run did not record, the source-side value SHALL be left
empty. Substituting the page's current value, or this page's default, is
FORBIDDEN — that invents a baseline for a run that never recorded one.

Values that differ only in numeric spelling (the source comes from YAML, the
submitted value from a form widget) SHALL NOT be reported as changes; a boolean
compared with a number SHALL be reported (they are not the same configuration
semantics). Machine-local keys SHALL be excluded, the same exclusion preset
comparison uses.

A run-scoped key SHALL NOT be reported as outside the page's submit schema:
it is absent from every submitted configuration by construction, so reporting
it puts a standing false warning on every rerun.

Absence of value divergence SHALL be stated affirmatively, not left silent: an
operator must be able to tell "verified identical" from "nobody checked".

#### Scenario: a field edited after the prefill is named at review time

- **GIVEN** a rerun prefill followed by an operator edit to one field
- **WHEN** the pre-launch review renders
- **THEN** that field is listed with the source run's value and the value about
  to be submitted

#### Scenario: schema drift does not masquerade as a value change

- **GIVEN** a rerun from an older run that did not record a field the page now
  submits, and a different run mode than the one selected
- **WHEN** the pre-launch review renders
- **THEN** the unrecorded field and the other-mode field appear in their own
  groups, with the unrecorded field's source value shown as absent rather than
  filled in

#### Scenario: a removed legacy key is reported once, not contradicted

- **GIVEN** a rerun from a run whose configuration records a key belonging to
  neither mode's current schema
- **WHEN** the pre-launch review renders
- **THEN** that key appears only in the unsupported-field report, and is not
  also described as belonging to the other mode

#### Scenario: switching the run mode is disclosed

- **GIVEN** a rerun prefill from a walk-forward run, with the page switched to
  pipeline mode and every shared field left as prefilled
- **WHEN** the pre-launch review renders
- **THEN** the mode change is listed as a value difference rather than the
  configuration being called identical

#### Scenario: an unedited rerun states its equality

- **GIVEN** a rerun prefill whose every submitted field matches the source run
- **WHEN** the pre-launch review renders
- **THEN** the page affirms the configuration matches that run field for field,
  rather than showing nothing

### Requirement: The rerun action SHALL be reachable from every result engine

The rerun entry point SHALL be rendered for walk-forward results as well as
pipeline results, through ONE shared implementation.

It previously lived only inside the pipeline dashboard's action bar, which the
walk-forward dispatch branch never calls. A normal walk-forward result — one
carrying `walk_forward_report.json` and no root `pipeline_report.json` — could
therefore never produce the prefill state at all, so every requirement written
here about a walk-forward source run was unreachable from that side.

Two copies of the action are FORBIDDEN. Either copy that forgot to mint the
action identity, or to record the source run's mode, would present as "the
prefill did not seem to apply" — a symptom that does not look like a defect.

#### Scenario: a walk-forward result offers the rerun action

- **GIVEN** a result whose run directory holds `walk_forward_report.json` and
  no root `pipeline_report.json`
- **WHEN** the results page renders it
- **THEN** the rerun action is available

#### Scenario: a walk-forward run without its report still offers it

- **GIVEN** a walk-forward result whose report is not present yet
- **WHEN** the results page renders it
- **THEN** the rerun action is still available, because that is exactly when an
  operator wants to adjust parameters and run again

### Requirement: A prefilled date SHALL bind to the widget, not merely to its default

Prefilled date fields SHALL be bound to the widget's own state, so that applying
a prefill replaces what the operator sees and what a launch submits — including
when the prefilled value equals the value the page would have defaulted to.

Passing the value only as the widget's default is not binding. An unkeyed
Streamlit widget is identified by its parameters: change the index and it
resets, leave the index unchanged and it retains whatever the operator last
chose. So the one case where the prefilled value coincides with the calendar
default silently keeps the operator's earlier date while the banner states that
the source run overwrote the fields — the launched window differs from the
reselected run with nothing on screen to show it.

Keying the widget alone is equally wrong in the opposite direction: session
state then wins and the index argument is ignored, freezing the live default so
a provider change no longer recomputes the window. That failure was already
shipped once and rolled back.

The binding SHALL therefore write the widget's state exactly when the wanted
value changes or a new prefill action arrives, and at no other time.

A new action SHALL force a widget only for fields the CURRENT payload actually
supplied. A source run whose archived config is validly empty, fails to parse,
or predates a date field still mints a fresh action, and forcing every date
widget on that action silently discards the operator's existing date edit — while
the page is saying that nothing, or only the recorded fields, was prefilled. These
properties SHALL be verified by driving the real widget through a sequence of
interactions, not by calling the pure default-resolving helper: that helper
never instantiates a widget, so the defect is invisible beneath it.

#### Scenario: a prefill equal to the calendar default still applies

- **GIVEN** an operator who edited the walk-forward window after it rendered
- **WHEN** they rerun from a source run whose date equals the calendar default
- **THEN** the widget shows the source run's date

#### Scenario: an edit made after the prefill is not undone

- **GIVEN** a prefill has been applied
- **WHEN** the operator edits the date and the script reruns
- **THEN** the edit survives

#### Scenario: an out-of-calendar default does not overwrite every frame

- **GIVEN** a configured date outside the active provider's calendar, snapped to
  the calendar's first entry with a visible warning
- **WHEN** the operator then selects a valid date and the script reruns
- **THEN** their selection survives

#### Scenario: a validly empty archive still carries the source run's engine

- **GIVEN** a source run whose archived config parses to an empty mapping, and
  whose mode is recorded in the job ledger
- **WHEN** the operator reruns from it
- **THEN** the page switches to that engine and says so

#### Scenario: an action that supplied nothing leaves the edit alone

- **GIVEN** an operator who edited a date, and a rerun whose payload does not
  carry that field
- **WHEN** the action arrives
- **THEN** the widget keeps the operator's date

#### Scenario: the live default still recomputes without a prefill

- **GIVEN** no prefill payload
- **WHEN** the provider changes and the calendar-derived default changes with it
- **THEN** the widget follows the new default

### Requirement: One predicate SHALL govern every prefill decision

Whether a rerun counts SHALL be decided by a single named predicate — a payload
is present AND it parsed — and every decision that depends on it SHALL read that
one predicate: applying the values, initialising the preset, and rendering the
source-run review.

Restating the condition at each site is how it drifts. A validly empty archive
still carries the source run's engine from the job ledger, so a site still keyed
on "how many fields parsed" will fight the others: preset initialisation reapplies
the default config and puts the engine back, or the review section renders nothing
while the banner promises a comparison.

The overwrite ledger SHALL report the value the operator could actually see. A
date field's visible value lives under the widget's own key once edited, while
the backing key still holds what the prefill or default wrote; reading only the
backing key reports a prior value that was never on screen, and — when the new
value happens to equal it — reports no overwrite at all even though the visible
selection is replaced.

#### Scenario: a validly empty archive keeps its engine through initialisation

- **GIVEN** a fresh session and a rerun from a walk-forward run whose archived
  config parses to an empty mapping
- **WHEN** the page initialises
- **THEN** the engine is walk-forward, not the default preset's

#### Scenario: the overwrite ledger names the visible prior value

- **GIVEN** an operator who edited a date after a prefill
- **WHEN** another prefill replaces it
- **THEN** the ledger reports the edited value as the prior one

### Requirement: A present-but-empty archive SHALL reach validation

The rerun action SHALL be enabled on whether the archived config EXISTS, not on
whether it has content, and the prefill parser SHALL report a present-but-empty
payload rather than treating it as no request at all.

A zero-byte `config.yaml` reads back as empty bytes, indistinguishable from a
missing artifact under a content test. Gating on content therefore disables the
action permanently with no explanation — while an empty YAML document's top
level is not a mapping, a shape this page already promises to report.

Presence SHALL be decided by the session key existing, not by its value being
truthy: the value of a zero-byte archive IS empty, so a truthiness test collapses
"the operator asked for a rerun" into "they did not".

#### Scenario: a zero-byte archive explains itself

- **GIVEN** a source run whose archived config exists and is zero bytes
- **WHEN** the operator reruns from it
- **THEN** the action is available and the page reports that the archive is
  empty, rather than silently doing nothing
