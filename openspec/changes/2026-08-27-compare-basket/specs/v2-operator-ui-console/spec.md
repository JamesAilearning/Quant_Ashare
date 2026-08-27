# Delta for v2-operator-ui-console

## ADDED Requirements

### Requirement: Run views SHALL be able to hand a selected run to the comparison page

A view that shows a single research run SHALL offer a way to carry that run to
the research run comparison page, so choosing runs to compare does not require
transcribing run IDs into that page's selector by hand. This applies to the
jobs list, the results view, and the walk-forward view.

Runs SHALL accumulate across views within the session, and the handoff SHALL
use the comparison page's existing `run_ids` URL parameter. The accumulated
selection SHALL NOT exceed what that parameter's validator accepts, and the
handoff link SHALL NOT be offered below the comparison page's own minimum
selection: offering a link that the destination will reject moves the refusal
one page later without preventing it.

The selection SHALL store the ID the comparison page will actually use, so the
count shown at the entry point and the selection shown at the destination
cannot disagree.

#### Scenario: runs picked in different views compare together

- **GIVEN** an operator who added one run from the jobs list and another from
  the walk-forward view
- **WHEN** they follow the comparison link
- **THEN** the comparison page opens with both runs selected

#### Scenario: a single run offers no comparison link

- **GIVEN** exactly one run accumulated
- **WHEN** the selection is rendered
- **THEN** no comparison link is offered, and the page states how many more
  runs are needed

### Requirement: A run that the comparison page cannot address SHALL be refused at the entry point, with its reason

Before offering to carry a run, a view SHALL determine whether the comparison
page can address it, and SHALL refuse the handoff otherwise. Routing an
unaddressable run to the comparison page is FORBIDDEN: that page stops on an
error, leaving the operator on a dead page instead of on the view they were
reading.

The refusal SHALL name which kind of unaddressable the run is — the artifact
directory taken over by a newer run, a run type the comparison page does not
accept, a run with no recorded artifact directory, or a run absent from the
unified job catalog. A bare "unavailable" is not a faithful report: these call
for different operator actions.

Addressability SHALL be derived from the comparison page's own catalog rather
than recomputed. That catalog's ownership rules (producer-recorded UI/CLI
relations, with timestamps as lifecycle evidence only) restated at an entry
point would drift from the destination that enforces them.

Where a run is addressable only under a different ID, the entry point SHALL
disclose that ID before adding it, so the run named at the entry point and the
run shown at the destination are never silently different.

Addressability SHALL be re-established against the CURRENT catalog before the
handoff is offered, not only when a run is added. The selection outlives the
moment it was made, and in the meantime a newer run can take over the same
artifact directory, or two stored runs can come to resolve to one owner — which
the destination refuses as a duplicate. A member that can no longer be handed
off SHALL be reported with its reason and SHALL block the handoff, rather than
being silently dropped: dropping it decides on the operator's behalf that the
run is no longer wanted, when they may be asking where it went.

#### Scenario: a member superseded after it was added blocks the handoff

- **GIVEN** an accumulated run whose artifact directory has since been taken
  over by a newer run
- **WHEN** the selection is rendered
- **THEN** that member is named with its reason and no comparison link is
  offered, rather than a link that the comparison page would refuse

A run whose ID cannot survive the handoff parameter SHALL also be refused at
the entry point. Catalog membership is not sufficient: catalog IDs are only
structurally validated, while the handoff parameter has its own whitelist and
its own separator. The check SHALL be a full round trip — the ID composed into
a request and parsed back the way the destination parses it must yield exactly
that ID — because a mere validator call accepts an ID containing the parameter's
separator, which the destination then reads as two different runs.

The comparison action SHALL be offered at a point every run view reaches,
before any early exit that skips artifact rendering. A run whose report is
absent or incomplete — running, partial, or empty — is precisely the one an
operator most wants to set aside for later comparison; placing the action after
such an exit removes it from that case and also hides an already-accumulated
selection.

#### Scenario: an ID the handoff parameter cannot carry is refused

- **GIVEN** a catalogued run whose ID contains a character the handoff
  parameter does not accept, or its separator
- **WHEN** the view renders its comparison action
- **THEN** the action is unavailable and the view says the ID cannot be carried

#### Scenario: a run with no report still offers the comparison action

- **GIVEN** a selected walk-forward run whose report contains no folds
- **WHEN** the view renders
- **THEN** the comparison action and any accumulated selection are still shown

#### Scenario: a superseded run is refused where the operator is standing

- **GIVEN** a run whose artifact directory is now owned by a newer run
- **WHEN** the view renders its comparison action
- **THEN** the action is unavailable and the view states that the directory was
  taken over, rather than routing to a page that stops on an error

#### Scenario: an aliased run discloses the ID it will compare as

- **GIVEN** a run whose current artifacts are held under a different catalog ID
- **WHEN** the view renders its comparison action
- **THEN** it names that ID before the run is added
