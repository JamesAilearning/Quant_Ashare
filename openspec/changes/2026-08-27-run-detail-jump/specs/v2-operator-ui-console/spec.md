# Delta for v2-operator-ui-console

## ADDED Requirements

### Requirement: A running run's detail page SHALL offer one jump to its live state

A detail page that can display a running run SHALL render a single
"前往作业" entry when — and only when — the run it is displaying is
**running**, and that entry SHALL carry both the run's id and the
running-status filter so the jobs list opens on that row rather than on
an unfiltered catalog.

A page that CANNOT display a running run SHALL NOT render the entry. The
walk-forward detail page is such a page: a job's artifact directory is
recorded only after its child process succeeds, and that page lists only
runs that have one — so a running run is absent from every table it
builds. Drawing the entry there would add a branch that never fires, and
the only way to test it is to fabricate a state production never
produces. Where the entry is omitted for this reason, the omission SHALL
be pinned to the premises that make it true, so that a change to any of
them reopens the question.

The verdict SHALL reuse the status word the producer already wrote into
the job record (the `job_io` normalized vocabulary the jobs-page filter
uses), matched exactly as the results page already matches it for its
auto-refresh toggle. The page SHALL NOT define a second spelling of
"running" — a second normalization (trimming, synonym widening) makes
the same run count as running on one surface and not on the other.

When the status is anything else, or the run id is missing or unusable,
the entry SHALL NOT be rendered at all. A link that is certain to land on
an empty or wrong filter SHALL NOT be drawn: it looks like an answer.
Absence is the fail-loud form here, because the entry is an accelerator —
every destination it offers stays reachable by hand.

The verdict and the link shape SHALL have a single implementation, shared
by every page that offers the entry. Per-page copies of "is it running"
plus a hand-assembled query string diverge; this repository has already
paid for that once with the per-page catalog fold.

The entry SHALL be a link only. It SHALL NOT introduce polling, auto
refresh, re-runs, or any write to job or artifact state, and it SHALL NOT
alter the results page's existing opt-in auto-refresh toggle.

#### Scenario: a running run gets the jump

- **GIVEN** a detail page displaying a run whose recorded status is running
- **WHEN** the page renders
- **THEN** it offers one jobs-page entry carrying that run's id and the
  running-status filter

#### Scenario: a finished run gets nothing

- **GIVEN** a detail page displaying a run in a terminal state
  (completed / failed / partial / stopped), or one not yet started
  (queued / pending)
- **WHEN** the page renders
- **THEN** no jump entry is drawn

#### Scenario: an unusable run id draws no link

- **GIVEN** a running run whose id is missing, blank, or would be rejected
  by the console's query-param schema
- **WHEN** the page renders
- **THEN** no jump entry is drawn, rather than a link that would arrive
  with an empty search box

#### Scenario: a page that cannot show a running run omits the entry

- **GIVEN** a detail page that lists only runs with a recorded artifact
  directory, which a running run does not yet have
- **WHEN** that page renders
- **THEN** it offers no jump entry, and the premises making a running run
  unreachable there are pinned

### Requirement: The jump SHALL carry filters the jobs page actually honours

Every query parameter the jump carries SHALL be validated on the
**departing** side against the same `_param_guard` schema the jobs page
applies on arrival, and the entry SHALL NOT be drawn when a value fails
it. The jobs page silently falls back to the default for any parameter
that fails its schema, so an unvalidated link degrades into "all running
jobs" while the operator believes they are looking at the one run they
opened.

The status value the jump carries SHALL be a member of the jobs page's
own status-filter domain and SHALL differ from that filter's default —
a jump whose status equals the default only opens the jobs page, and the
words "运行中" on the entry would then be false.

The run id SHALL travel as the jobs page's free-text search parameter,
because that page has no run-id filter and its search is what matches
`JobSummary.run_id`.

The jump SHALL carry a fresh one-shot handoff token. The jobs page
replaces a page-local filter widget from the URL only when the URL value
differs from the one it last consumed, and `status=running` is precisely
a value an earlier visit may have left in the URL; without the token this
navigation would be silently ignored and the operator would land on their
previous filter selection. A malformed token SHALL be refused loudly at
the call site rather than dropped, because the jobs page discards it
silently and the link would quietly lose its override.

The one-shot override SHALL apply to EVERY filter this jump carries, not
only the status. An operator who followed this jump, then edited the
jobs page's search box, and then followed the jump for the same run again
sends an unchanged search value — which the ordinary path preserves the
edited widget for, so the page shows no row or another run's row while
the row-scoped actions (including stop) point at the wrong run.

The arriving URL SHALL remain the complete filter state: a key the link
does not carry SHALL be reset to its default, one-shot handling included.
The token exists only to defeat the widget stickiness that would otherwise
ignore a value the link does carry — it does not narrow what the link
asserts. A status-only queue link therefore clears the search box, which is
what lets it show every job in that status; preserving an unrelated search
would leave the operator on an empty list, concluding the queued item is
gone.

#### Scenario: the destination filter keeps exactly that run

- **GIVEN** the parameters the jump carries for a running run
- **WHEN** the jobs page's own filter is applied to a catalog containing
  that run, an unrelated running run, and a finished run whose id shares
  the same prefix
- **THEN** only the jumped-to run survives

#### Scenario: a value the arrival guard would reject is never sent

- **GIVEN** a run id that the console's query-param schema rejects
- **WHEN** the entry is evaluated
- **THEN** nothing is rendered, rather than a link whose search value
  would be silently dropped on arrival

#### Scenario: a stale identical filter does not swallow the navigation

- **GIVEN** an operator who previously left `status=running` in the jobs
  page URL and then changed that page's status widget
- **WHEN** they follow the jump for a running run
- **THEN** the one-shot token makes this navigation's status apply once,
  instead of the jump being silently ignored

#### Scenario: a repeat jump for the same run overrides an edited search

- **GIVEN** an operator who followed this jump, edited the jobs page's search
  box, and now follows the jump for the same run again
- **WHEN** the jobs page seeds its filters
- **THEN** this navigation's run id replaces the edited search value once

#### Scenario: a status-only link resets the search it does not carry

- **GIVEN** a one-shot link carrying only a status filter, followed while the
  operator's typed search is fully mirrored into the URL
- **WHEN** the jobs page seeds its filters
- **THEN** the search returns to its default, so the link shows every job in
  that status

#### Scenario: the same link behaves the same whatever the page's residue

- **GIVEN** the same status-only link followed twice, once with the page's
  remembered URL value matching the typed search and once with it empty
- **WHEN** the jobs page seeds its filters
- **THEN** both land on the same filter state

#### Scenario: a malformed handoff token is refused, not dropped

- **GIVEN** a caller passing a token that is not a valid one-shot token
- **WHEN** the link is built
- **THEN** the call fails loudly rather than emitting a link the jobs page
  would strip the token from

### Requirement: The one-shot handoff SHALL reset every key that decides membership

The handoff SHALL override every filter whose value decides WHICH rows appear
and WHERE the requested row sits, deriving that set by subtraction from the
page's own default table rather than listing it by hand.

The ordinary seeding branch only replaces a widget value when the arriving URL
value differs from the last one consumed. An operator who changed `type` to
`provider` before leaving, while `type`'s last-consumed value is still the
default `all`, therefore keeps `provider` when a link that omits `type` arrives
— and the requested run is filtered out, so a link promising an exact row lands
on an empty list. A stale `page` does the same: a single-row result sits on
page one.

This is the same sentence this change already records — the arriving URL IS the
complete filter state — applied to all of it rather than to two of its keys.

Presentation preferences (sort field, sort direction, auto-refresh) SHALL be
exempt: they change neither membership nor position, and resetting them would
decide something on the operator's behalf that the link never requested.

Exempting them from the override set is NOT sufficient to preserve them. The
page mirrors session state into the URL each frame, so once an operator settles
on a preference the URL carries it; a link that omits the key then makes the URL
read return the default, and the ORDINARY branch — seeing a changed URL value —
resets the preference anyway. Exempt keys SHALL therefore be skipped entirely on
the handoff frame, and only on that frame: skipping them while the token remains
in the URL would exempt them permanently, so the operator could no longer change
them at all.

Deriving the set by subtraction is load-bearing: a hand-written list makes every
future filter key a fresh instance of this defect, whose only symptom is "the
link does not show that row".

Where a widget is keyed SEPARATELY from the filter state it feeds, the handoff
SHALL reset that widget key too. Most filter widgets are keyed with the filter's
own state key, so one write settles both. A date input is not: it owns a
different key and the page copies its value back into the filter state on the
same frame, so resetting the filter alone is undone immediately and the exact-row
link still lands on an empty list. Every such shadowing pair SHALL be either
mirrored on handoff or exempt from it, enumerated constructively so a widget
added later cannot be forgotten.

#### Scenario: a stale non-search filter does not swallow the exact row

- **GIVEN** a Jobs page whose `type` widget holds a non-default value while its
  last-consumed URL value is the default
- **WHEN** the operator follows a detail-page link that omits `type`
- **THEN** `type` is reset to its default and the requested row is visible

#### Scenario: a shadowing widget key is reset with its filter

- **GIVEN** an operator who selected a date range, so both the filter state and
  the date input's own key hold it
- **WHEN** they follow a detail-page link for a run outside that range
- **THEN** both are cleared, and the requested row is visible

#### Scenario: presentation preferences survive the handoff

- **GIVEN** a Jobs page whose sort and auto-refresh have settled at non-default
  values, mirrored into the URL
- **WHEN** the operator follows a detail-page link that omits them
- **THEN** those preferences are unchanged

#### Scenario: an exempt key is only skipped on the handoff frame

- **GIVEN** a handoff whose token has already been consumed
- **WHEN** the operator changes the sort and the page reseeds from the URL
- **THEN** the new sort takes effect
