# Delta for v2-run-center-page

## ADDED Requirements

### Requirement: A session-launched manual update SHALL be cancellable through the live handle only

The run center SHALL offer a controlled cancel for a manual update it
launched in THIS UI session, and the cancel channel SHALL be the live
`Popen` handle carried on the launch result — never a pid (pids are
recycled by the OS; killing by number can hit an unrelated process). The
scheduler's automatic runs are not children of the UI and SHALL remain out
of reach. A UI restart loses the handle and the old run SHALL simply be
non-cancellable — an honest degradation, not an error.

The control SHALL be visible only while the held process is still alive
(`poll()` is None; an exited handle retires silently — the run's own status
and ledger speak for it), and SHALL require a two-step confirmation before
acting.

Cancellation SHALL never write the orchestrator's status artifact: on
Windows the process is terminated forcibly (measured in this repository:
`CTRL_BREAK_EVENT` cannot reach a `CREATE_NO_WINDOW` child from the UI
console, and Python maps CTRL_BREAK to SIGBREAK — immediate death, no
KeyboardInterrupt), so the artifact stays `running`; the page SHALL present
that state honestly from its own handle evidence (the process is confirmed
exited) instead of fabricating a terminal record. On POSIX the page's
runner SHALL send SIGINT to the process group first — the orchestrator's
own BaseException path then writes the terminal record — falling back to
SIGKILL after a grace window.

Every cancel attempt on a live process SHALL leave dated `[run_center]`
markers (request and outcome) in the shared log, following the launch
marker convention; a no-op cancel (the process had already finished) SHALL
leave no marker and change nothing — UNLESS pending context from a
previously issued kill stands: then the "already finished" death is
kill-caused, and the attempt SHALL route through the full late settlement
(outcome marker, swap diagnosis, evidence adoption) instead of the no-op
path, which would discard the handle and the pending context and lose the
entire epilogue. When a marker write fails (the log
became unwritable after launch), the termination itself still proceeds —
but the outcome SHALL report the missing audit trail and the page SHALL
warn loudly rather than letting the operator action go unrecorded in
silence.

The forcible-cancel presentation SHALL be conditioned on what the post-kill
reread actually found: only when a matching `running` record was found and
the evidence persisted may the page claim the artifact stays `running`,
will remain labelled cancelled, and that the gate unlocked — and that claim
SHALL additionally be conditioned on the evidence STILL covering the
current status record at render time: a scheduler replacement can write a
new `running` record between evidence persistence and the announcing
re-render, the page's top logic then retires the evidence and restores the
running gate, and repeating the historical "will stay labelled cancelled /
gate unlocked" wording would contradict the same frame's running banner and
disabled button. In that superseded case the page SHALL say the evidence
was retired and defer to the current status. A kill landing
before the child wrote its record finds no orphan — the page SHALL then say
exactly that and defer to the status artifact as-is. Adoption SHALL be
IDENTITY-BOUND AND TIME-BOUND to the killed run — both, conjunctively:

* **Identity**: the record's writer `pid` (stamped by the orchestrator
  into every status record as `os.getpid()`; the launcher spawns the
  orchestrator directly with no shell wrapper, so the held handle's
  `Popen.pid` IS the writer) SHALL equal the killed handle's pid. Time
  alone cannot carry this: a scheduler run can start after the session
  launch and win the single-flight lock BEFORE the UI child does — its
  `started_at` falls inside the time window while the killed UI child is
  merely the exit-17 loser, and time-only adoption would label the live
  scheduler run as cancelled. A record without a pid (written by a
  pre-upgrade orchestrator) SHALL never be adopted — fail-closed; the
  reader SHALL treat a present-but-malformed pid as corruption, never
  coerce it, with presence judged by the KEY, not the value: an explicit
  `"pid": null` is a malformed record under the new contract, not a
  legacy absence — rendering it as valid would leave a running record
  that cancellation evidence can never bind to, blocking relaunches to
  the staleness threshold after a hard cancel.
* **Time window**: the record's `started_at` must fall inside the
  [session-launch, kill-completion] window — the residual defence against
  pid reuse (the OS can recycle the killed pid for a later writer; inside
  the window that pid was provably held by the session's child). Both
  bounds SHALL be captured at their tight edges: the lower bound BEFORE
  the spawn call (the child can write its record before a post-spawn
  timestamp), and the upper bound AT the moment the cancellation boundary
  confirms the process dead — not after it returns, since the boundary
  still writes markers and inspects the filesystem afterwards and a
  replacement run can start inside that tail.

When the post-cancel swap-state inspection itself fails (volume gone,
permissions, I/O error), the outcome SHALL report the state as UNKNOWN —
a distinct third state, never silently healthy — and the page SHALL say it
cannot vouch for the online data and instruct manual verification. Failed
cancellations SHALL carry the marker-audit result exactly like successful
ones; the missing-audit warning SHALL cover both.

The live bundle is untouched by cancellation at any point OUTSIDE the
swap's two-rename window: the pipeline builds into a sidecar and only an
already-validated build is atomically swapped, so the serving data remains
the last successful update regardless of when the process dies. The swap
itself is crash-atomic by contract — a kill landing between its two renames
leaves the canonical directory momentarily absent, restored by the next
run's startup repair. The cancel SHALL therefore check for the crash-state
SIGNATURE after the process exits — canonical directory missing AND the
`.bak` sibling present (rename one done, rename two pending) — and report
the swap hit LOUDLY with the instruction to start another update
immediately; it SHALL NOT claim the online data was unaffected in that
state. Bare absence of the canonical directory is NOT the signature: a
bootstrap run legitimately starts with no live bundle, and its cancellation
SHALL NOT be misdiagnosed as an interrupted swap. Leftover
partials are cleaned by the next run's startup repair; the single-flight
lock is OS-held and releases with the process.

After a forcible cancel the page SHALL keep presenting the orphaned
`running` record as cancelled ACROSS reruns — the evidence is bound to that
record's exact `started_at` stamp and retires the moment the status is
CONCLUSIVELY superseded: a valid `running` record with a DIFFERENT stamp,
or a `finished` terminal record. An inconclusive read — a missing or
corrupt artifact from a transient volume or permission failure — SHALL NOT
retire the evidence: discarding it on a read failure lets the same orphan
reappear as live once access recovers and block relaunches to the
staleness threshold, while retained evidence is inert until a matching
`running` record shows again — and the launch gate SHALL unlock **all the way through the
runner boundary**: the launch path's own fresh-`running` refusal SHALL admit
exactly the record whose stamp equals the cancellation evidence (any new
run writes a new stamp and the bypass expires; the single-flight lock stays
the real arbiter). Unlocking only the page button while the runner still
refuses would be a fake unlock — the instructed immediate re-run after a
swap hit would bounce as already-running until the staleness threshold.
The evidence stamp SHALL be read from the status artifact AFTER the process
is confirmed dead, not from an earlier page snapshot: the child may write
its `running` record after the page render that armed the cancel, and a
stale stamp would make the exact-match evidence retire on the next rerun,
leaving the orphan blocking again. A failed cancel SHALL retain the live
handle: it is the only permitted cancellation credential, and discarding it
would leave no retry path. It SHALL also record the cancel-request moment
as pending context — the settlement trigger and audit stamp, NOT the
evidence bound: a kill can complete AFTER the failed attempt returns, and
the handle-retire path must then settle the evidence instead of treating
the late death as an ordinary self-completion that leaves the orphaned
record blocking launches. The adoption's PRIMARY identity SHALL be a
per-launch NONCE carried by the record itself: the launcher generates a
one-time nonce before the spawn, passes it to the child through its
environment, and the orchestrator writes it into EVERY status record it
produces — an identity that travels with the record covers records written
at ANY moment of the child's lifetime, including the tail window after the
last liveness observation that no observation scheme can close, and is
immune to pid reuse by construction (a recycled-pid successor never holds
this launch's nonce). A record bearing a DIFFERENT nonce SHALL be refused
outright; a record bearing NO nonce (a scheduler run, or an in-flight run
of a pre-upgrade orchestrator) falls back to the legacy chain below. The
legacy late-adoption identity is a LIFETIME-OBSERVED exact stamp: while the pending process is still provably
alive (alive-poll → status read → alive-poll — the pid is continuously
held between the two polls, so it cannot have been recycled), the page
records the `started_at` of the run's OWN `running` record (matching
provider and pid) as the adoption candidate — captured immediately BEFORE
the cancellation call (the call can consume the whole grace window and the
kill can land right after it returns, so a confirmation-time observation
alone races the death and can find only a corpse), captured INSIDE the
failed cancellation boundary at the moment the process is provably alive
(the timed-out wait and the alive re-check after a raising kill both prove
liveness — the record can be written after the pre-call observation while
the process dies before the post-call one, leaving both ends empty),
refreshed after the failed attempt and on every watcher tick; settlement
adopts only a record
whose stamp EQUALS that candidate and whose pid equals the killed
handle's. Neither a request-time bound nor a death-observation bound is
admissible: the request moment rejects a genuine record the child writes
after the request was captured, and the observation moment — up to one
polling period after the real death — admits a scheduler replacement that
received the recycled pid inside that gap. Absent a lifetime-observed
candidate, settlement SHALL NOT adopt (fail-closed: an orphan waiting out
the staleness threshold beats labelling a live replacement as cancelled).

The nonce-bound adoption proof SHALL survive an INCONCLUSIVE post-death
read: the killed run's nonce is known a priori — it is this session's own
launch identity — so persisting the evidence does not depend on that read
succeeding. When the settlement or immediate-cancel reread returns missing
or corrupt, the page SHALL persist nonce-only evidence (empty stamp)
before retiring the handle context; the orphan is then covered by nonce
the moment it becomes readable again, instead of blocking relaunches to
the staleness threshold. Evidence coverage, evidence retirement, and the launch gate's release
SHALL share ONE predicate in which the nonce identity, when present on
EITHER side, decides ALONE: a record whose `started_at` equals the
evidence stamp but whose nonce differs or is absent (a coarse or frozen
system clock can produce equal stamps) is NOT covered — treating it as
covered would label a live replacement as cancelled and release the gate
for it — and conversely a nonce-bearing record is never claimed by
nonce-less legacy evidence. The exact-stamp comparison survives only for
pairs where NEITHER side carries a nonce (a pre-upgrade in-flight
session). The adoption fallbacks obey the same rule: when this session
holds a launch nonce, its child provably writes that nonce, so a
nonce-less record is never adopted through the legacy pid/stamp path.
Releasing or covering by stamp alone would recreate the fake-unlock the
gate bypass exists to prevent — in the opposite direction, unlocking for
a live replacement. The conclusive-read wording (evidence found and persisted) SHALL
remain conditioned on a conclusive read; nonce-only evidence stays silent
until a matching record appears, and is inert when none ever does.

Late settlement SHALL owe the FULL cancel epilogue, not just the evidence:
the same outcome marker (labelled as a late exit), the same strict
swap-state inspection with its interrupted/unknown outcomes, and the same
outcome presentation the immediate path uses — a late death landing inside
the swap's two renames must surface the loud repair instruction, never be
retired as a clean cancellation. The shared epilogue SHALL be one
implementation, not a re-transcription that can drift.

Pending context SHALL be recorded only when a kill was ACTUALLY ISSUED (the
kill call returned without raising): the two failure modes of a cancel are
semantically opposite for a later death — after an issued-but-unconfirmed
kill the late death is cancellation-caused and settles as cancelled, while
after an attempt in which EVERY signal call raised, the process was never
touched and its later natural completion SHALL retire as an ordinary
self-completion, never be settled (and marked, and swap-diagnosed) as a
forcible cancel that did not happen. A POSIX process-group signal call
that RETURNS SUCCESS is proof of issuance — the kernel accepted delivery —
and SHALL count as issued even when the fallback single-process kill then
races the exit it caused and raises: hardcoding that path as not-issued
would retire a death caused by the successfully delivered group SIGKILL
as natural and skip the entire late epilogue.

The converse SHALL hold symmetrically: an exit observed when NO signal was
successfully issued is a natural completion, never a cancellation. A child
that was alive at the initial check but finishes on its own while every
signal call raises (on POSIX, a natural exit racing the polite signal —
the grace-window death would otherwise classify as graceful; on Windows,
a death inside the check-to-kill window) SHALL be reported as already
finished — with an outcome marker closing the already-written request
marker, and with the marker-loss warning covering this outcome — and SHALL
NOT receive the cancellation-specific graceful claim, swap diagnosis, or
orphan adoption; its terminal state is the run's own status artifact and
ledger. The graceful classification SHALL therefore be possible only after
the polite signal was actually issued.

When the kill call itself raises, the attempt SHALL RE-CHECK the process
before classifying the raise: the child can exit in the interval between
the liveness check and the kill, making the raise a handle-already-terminal
artifact rather than a failure. A raise with the process confirmed dead
SHALL flow into the same classification (no signal issued → already
finished; a signal issued → the confirmed-death cancellation epilogue) and
SHALL NOT be reported as a cancellation failure — that would retain a dead
handle and present a natural completion as a failed cancel. Only a raise
with the process still alive is a genuine failure.

A NORMAL RETURN from the single-process kill is likewise not conclusive
delivery evidence: the underlying implementation polls first and silently
sends nothing when the process is already terminal. The attempt SHALL
re-check after the return — still alive proves the signal was sent (the
internal poll saw a live process); found dead with no prior signal is the
ambiguous micro-window, resolved by the terminal-record oracle (the same
pid + launch-to-exit verification) COMBINED with a PRE-KILL terminal
snapshot. A matching terminal record that ALREADY existed before the kill
call — the child alive past its terminal write, appending the ledger — is
an UNDECIDABLE cell: the snapshot proves record ordering, not delivery
(the kill may have terminated the live child, or the child may have exited
naturally between the snapshot and the terminate call, which then returns
silently — indistinguishable through the process API). That cell SHALL be
reported as its own outcome, in words that commit to neither "nothing was
cancelled" nor "termination executed": the run's terminal record predates
the kill, delivery of the terminate call cannot be determined, and the two
possibilities are data-equivalent (terminal state recorded; ledger append
not guaranteed). Only a terminal record that APPEARS after the kill call
proves the child finished naturally inside the poll-to-kill window →
already finished; absent both → classified as a signalled confirmed death,
whose epilogue (artifact reread, orphan adoption or the honest no-orphan
presentation) treats even a recordless natural crash correctly.

EVERY attribution of an artifact record to the cancelled run — running
adoption and terminal verification alike, on both the immediate and the
late path — SHALL be NONCE-ONLY. For a nonce-less run there is NO
non-replayable identity: the pid is recyclable and every timestamp
quantity (windows, exact stamps, lifetime-observed candidates) can be
replayed by a same-pid successor under a frozen or coarse clock; and every
read that could attribute happens AFTER work that leaves a reuse interval
(the cancel boundary writes markers and inspects the filesystem after
confirming death; the settlement read follows a watcher tick). Legacy
attribution SHALL therefore fail closed: the orphaned record is left to
the staleness threshold, the page presents the artifact as-is with no
attribution claim, and the run's own status artifact and ledger remain its
only account. This costs a nonce-less run — only reachable for a child
launched by a pre-upgrade UI still in flight across a hot upgrade — its
cancellation-evidence labelling, an honest degradation of the same kind as
losing the handle on a UI restart.

Every render-frame revalidation of a previously verified record (the
graceful terminal claim, the terminal-after-kill claim) SHALL distinguish
an INCONCLUSIVE current read from a genuine replacement, exactly as
evidence retirement does: a missing or corrupt artifact at render time
supports only "currently unreadable — the terminal state stands as
verified at cancel time", never "superseded by a later record", which is
reserved for a valid record that fails the identity match. With this,
the kill call's observation space is exhaustively partitioned —
{raise, return} × {alive, dead} each has an explicit classification and
none rests on an assumption about the call's semantics. The pending context SHALL also carry the failed
attempt's marker-audit state, and late settlement SHALL aggregate it rather
than reset to the optimistic default: when the request/failure markers never
reached the log, a successfully written late outcome marker does not make
the audit trail complete, and the settled result SHALL keep reporting the
gap. The same aggregation SHALL apply to every RETRY of the cancel while
pending context stands: a retry that terminates the process with its own
markers written still reports the audit trail as incomplete when the
earlier live attempt's markers were lost, and a repeated timeout never
overwrites a stored marker failure with the newer attempt's success — the
warning speaks for the WHOLE audit chain, not the latest attempt. The
accumulated marker state SHALL be updated by EVERY retry made while
pending context stands, including a retry that issues no additional kill
(its kill call raised): that retry's lost markers belong to the same
pending cancellation's audit chain, and leaving the stored state untouched
would let the eventual late settlement start from a stale success and
report a complete chain. Such a kill-less retry SHALL NOT refresh the
pending identity itself — the pending kill remains the earlier one.

A graceful outcome's claim that the orchestrator wrote its own terminal
record SHALL be VERIFIED, never inferred from timely process death: the
polite signal can land while the orchestrator is still importing modules,
parsing configuration, or acquiring the single-flight lock — before its
terminal-record path is armed — and the process still exits within the
grace window while the artifact stays missing, stale, or still `running`.
Verification SHALL apply the same identity-decides rule as coverage: when
the session holds a launch nonce (or the record bears one), the `finished`
record confirms the run only if it bears THAT nonce alongside the matching
pid — the time window is no longer a criterion there, because pid reuse
combined with a frozen or coarse host clock can let an OLDER finished
artifact satisfy pid and window together, while the nonce is unique to
this launch and unreachable to stale or replacement artifacts. Only for
nonce-less legacy pairs SHALL verification fall back to the original
conjunction: a `finished` record whose writer pid equals the killed
handle's pid AND whose `started_at`/`finished_at` both fall inside the
session's launch-to-exit window. Anything else — a running record, a
pid-less record, a nonce mismatch in either direction, out-of-window or
unparseable or zone-naive stamps in the legacy pair, a missing or corrupt
artifact, an unverifiable provider, an absent window bound — SHALL read as
not-confirmed. A graceful exit WITHOUT a confirmed terminal record SHALL
flow through the same orphan-adoption presentation as a forcible kill
(worded honestly as a polite exit whose terminal record was not confirmed),
because such an exit can leave the same orphaned `running` record a hard
kill leaves.

While a cancellation is pending, the page SHALL watch the retained handle
itself and settle AUTOMATICALLY when it exits: after a hard kill the status
signature and the log progress can both stay frozen, so a watcher comparing
only those never fires and the settlement would wait for a manual
interaction — leaving dead-process cancel controls and an orphaned
`running` record standing indefinitely. Settling SHALL immediately re-render
the page so the corrected state (not the pre-settlement banner) is what the
operator sees.

The settlement SHALL execute BEFORE the watcher registers in the page's
execution order: the watcher fragment also runs inline on every full page
execution, and its dead-handle branch aborts the current execution with a
re-render request — settlement placed after the fragment would never be
reached, each fresh execution hitting the same dead-handle branch first in
an endless re-render loop that neither settles the evidence nor retires
the handle. With settlement ahead of the fragment, the dead pending handle
is settled (and the pending state cleared) before the fragment can observe
it, so the loop cannot form; a handle that dies mid-execution after the
settlement block merely hides the cancel controls for that pass and is
settled by the next execution, which the watcher's polling raises within
one period.

#### Scenario: an accidental click after the run already finished

- **GIVEN** the operator cancels a session-launched run that has already
  exited
- **WHEN** the cancel executes
- **THEN** nothing is killed, no marker is written, and the page says the
  run had already finished — its own status and ledger stand

#### Scenario: cancelling a live run on Windows

- **GIVEN** a live session-launched manual update on Windows
- **WHEN** the operator confirms the cancel
- **THEN** the process is terminated, the log carries the request and
  outcome markers, and the page states that the status artifact remains
  `running` because a force-killed process cannot write its terminal
  record — presented as handle-evidenced truth, not as a live run

#### Scenario: cancelling a live run on POSIX

- **GIVEN** a live session-launched manual update on POSIX
- **WHEN** the operator confirms the cancel
- **THEN** SIGINT reaches the process group first and, when the orchestrator
  exits within the grace window, the cancel reports a graceful outcome whose
  terminal record was written by the orchestrator itself

#### Scenario: a cancel landing inside the swap window

- **GIVEN** a cancel whose kill lands between the swap's two renames
- **WHEN** the cancel completes and the canonical directory is missing
- **THEN** the outcome reports the swap hit loudly, instructs an immediate
  re-run (startup repair restores the directory), and does not claim the
  online data was unaffected

#### Scenario: a replacement landing before the announcement is not contradicted

- **GIVEN** a forcible cancel whose evidence persisted, with a scheduler
  replacement writing a new `running` record before the announcing
  re-render
- **WHEN** the page renders the cancel outcome
- **THEN** it does not claim the record will stay labelled cancelled or
  that the gate unlocked — it says the evidence was retired and defers to
  the current status, consistent with the running banner and disabled
  button in the same frame

#### Scenario: the cancelled record stays cancelled across reruns

- **GIVEN** a forcible cancel left the status artifact at `running`
- **WHEN** the page reruns any number of times while that record stands
- **THEN** it keeps presenting the record as cancelled on handle evidence,
  the launch gate stays unlocked, and the evidence retires when a new run
  writes a different stamp

#### Scenario: a failed cancel keeps the credential

- **GIVEN** a cancel attempt that reports failure with the process possibly
  alive
- **WHEN** the page renders the failure
- **THEN** the live handle is retained so the operator can retry

#### Scenario: a lock-stealing scheduler run is not adopted as evidence

- **GIVEN** a scheduler run started after the session launch and won the
  single-flight lock before the UI child, whose kill then lands while the
  scheduler's `running` record stands with an in-window `started_at`
- **WHEN** the post-kill reread inspects that record
- **THEN** adoption is refused — the record's writer pid differs from the
  killed handle's pid — and the live scheduler run keeps rendering as
  running with the launch gates closed

#### Scenario: a late death inside the swap window is not retired cleanly

- **GIVEN** a failed cancel whose kill completes late, with the death
  landing between the swap's two renames
- **WHEN** the pending settlement runs
- **THEN** it performs the same strict swap-state inspection as an
  immediate cancel, reports the swap hit loudly with the immediate re-run
  instruction, and writes the late-exit outcome marker to the log

#### Scenario: an identity-only change wakes the watcher

- **GIVEN** persisted cancellation evidence and a same-stamp replacement
  whose only observable difference is its launch nonce or writer pid
- **WHEN** the watcher fragment polls the status artifact
- **THEN** the identity fields — part of the watched signature — differ
  from the baseline and trigger a full re-render, so the page stops
  presenting the replacement as cancelled without waiting for an
  interaction

#### Scenario: a finished record surviving a hard kill is named as such

- **GIVEN** a hard kill landing after the orchestrator wrote its matching
  terminal record but before it finished appending the ledger — whether
  the death is confirmed immediately or arrives late after a timed-out
  kill and is settled by the watcher
- **WHEN** the post-kill (or settlement-time) reread finds that `finished`
  record and the terminal oracle confirms its identity
- **THEN** the page reports that the run completed its terminal record
  before termination — possibly only the ledger append was interrupted —
  instead of claiming the process was killed before writing any record,
  which would contradict the finished banner in the same frame; both the
  immediate-confirmation and the late-settlement paths run the same
  oracle, applied to the SNAPSHOT each path already read — never through a
  second artifact read, which a scheduler replacement can overwrite
  between the snapshot and the recheck, flipping the detection false
  against the very record the frame displays; and the announcing render —
  a re-render after the detection frame — SHALL re-verify that the current
  banner still IS the accepted record (its stamp and nonce carried in the
  outcome) before claiming so, saying instead that the terminal record was
  verified at cancel time but has since been superseded when they differ —
  and that superseded wording SHALL carry the terminal facts verified at
  cancel time (run date, start stamp, exit code) and SHALL NOT direct the
  operator to the ledger as authoritative: the very window this outcome
  represents is a kill between the terminal status write and the ledger
  append, so the ledger may never have received the entry. More generally, NO cancel
outcome wording may present the ledger as verified or authoritative: the
orchestrator's ledger append is deliberately best-effort (a failed append
is swallowed and the process exits normally), and this page verifies only
the status artifact — ledger mentions SHALL be qualified as best-effort
and unverified. And an absent matching record on the post-kill reread
SHALL NOT be presented as proof the child was killed before writing: the
only verified fact is that no matching record exists NOW — the child may
have written its record and had it superseded between its death and the
reread, or the artifact may be momentarily unreadable — and the wording
SHALL name these possibilities instead of asserting a pre-write kill

#### Scenario: a same-stamp replacement with another identity is not covered

- **GIVEN** persisted cancellation evidence bearing this launch's nonce,
  and a replacement `running` record whose `started_at` happens to equal
  the evidence stamp under a coarse system clock but whose nonce differs
  or is absent
- **WHEN** the page evaluates coverage, retirement, and the launch gate
- **THEN** the replacement is not covered — it renders as live, the
  evidence retires as conclusively superseded, and the gate stays closed

#### Scenario: an unreadable artifact at settlement does not orphan the proof

- **GIVEN** a settled (or immediately confirmed) kill whose post-death
  status read returns corrupt because the volume is briefly unavailable
- **WHEN** the artifact becomes readable again and the orphaned `running`
  record reappears
- **THEN** the nonce-only evidence persisted before retirement covers it —
  the record renders as cancelled and the launch gate releases by nonce —
  instead of presenting it as live for six hours

#### Scenario: a record written after every observation is still claimed

- **GIVEN** a timed-out kill whose child writes its `running` record after
  the boundary observation and dies before the cancel call returns — the
  pre-cancel, boundary, and post-call observations all empty
- **WHEN** the pending settlement binds the evidence
- **THEN** the record is claimed by its launch nonce — written into it by
  the child itself, with no observation window — and the orphan does not
  block relaunches; a record bearing another launch's nonce is refused

#### Scenario: a record written after the cancel request still settles

- **GIVEN** a cancel confirmed in the window after the spawn but before the
  child wrote its `running` record, whose kill times out while the child
  writes that record, with the death arriving late
- **WHEN** the watcher observes that record while the process is still
  provably alive and the pending settlement later binds the evidence
- **THEN** the record — its stamp equal to the lifetime-observed candidate,
  its pid equal to the killed handle's — is adopted, and the orphan does
  not block launches to the staleness threshold

#### Scenario: a replacement on a recycled pid is not adopted late

- **GIVEN** a killed child whose death the watcher observes up to one
  polling period late, while a scheduler replacement received the recycled
  pid and wrote its own `running` record inside that gap
- **WHEN** the pending settlement binds the evidence
- **THEN** the replacement's record is refused — its stamp equals no
  lifetime-observed candidate — and the live run keeps rendering as
  running with the launch gates closed

#### Scenario: a stale terminal artifact with a reused pid is not verified

- **GIVEN** a launch whose child received the same pid stored in an older
  `finished` artifact and was killed before writing any status — with the
  host clock coarse or frozen enough that the old artifact's stamps also
  satisfy the launch-to-exit window
- **WHEN** the graceful outcome verifies the terminal record
- **THEN** verification fails on the nonce — the stale artifact does not
  bear this launch's nonce — and the page does not claim the orchestrator
  wrote a terminal record

#### Scenario: a silent kill return does not fabricate delivery evidence

- **GIVEN** a child that terminates naturally inside the pre-kill window,
  whose kill call then returns normally without sending anything, and
  whose own terminal record stands verified
- **WHEN** the attempt re-checks and consults the terminal-record oracle
- **THEN** the outcome is "already finished" — the natural completion is
  not presented as a cancellation — and absent such a record the death is
  classified as signalled and receives the cancellation epilogue

#### Scenario: a kill raising against a corpse is not a failed cancel

- **GIVEN** a child that exits in the interval between the pre-kill
  liveness check and the kill call, whose kill then raises against the
  terminal handle
- **WHEN** the attempt re-checks and confirms the death
- **THEN** the raise is classified like any confirmed death — already
  finished when no signal was issued, the cancellation epilogue when one
  was — and no cancellation failure retaining a dead handle is reported

#### Scenario: an unsignalled death is reported as finished, not cancelled

- **GIVEN** a cancel whose child was alive at the initial check but
  finished naturally while every signal call raised
- **WHEN** the attempt observes the death
- **THEN** the outcome is "already finished" — no graceful claim, no swap
  diagnosis, no orphan adoption — with an outcome marker closing the
  request marker, and the run's own status artifact speaks for its end

#### Scenario: a group kill racing the fallback still counts as issued

- **GIVEN** a POSIX cancel whose group SIGKILL returned success, with the
  fallback single-process kill racing the exit it caused and raising
- **WHEN** the attempt returns as failed and the process dies late
- **THEN** the attempt counts the kill as issued, pending context is
  recorded, and the late death settles with the full epilogue instead of
  retiring as a natural completion

#### Scenario: a candidate observed before the cancel survives the race

- **GIVEN** a run whose `running` record stands, with a timed-out kill
  finishing in the interval after the cancel call returns but before the
  page's post-attempt observation
- **WHEN** the pending settlement later binds the evidence
- **THEN** the pre-cancel lifetime observation supplies the candidate, the
  genuine orphan is adopted, and relaunches are not blocked to the
  staleness threshold

#### Scenario: a failed kill call does not turn a natural finish into a cancel

- **GIVEN** a cancel attempt whose kill call itself raised, leaving the
  process untouched
- **WHEN** that process later finishes naturally and the page retires the
  handle
- **THEN** no late settlement runs — no cancel outcome, no late-exit
  marker, no swap diagnosis — and the run's own status artifact speaks
  for its completion

#### Scenario: a retry's success does not erase the first attempt's audit gap

- **GIVEN** a first cancel whose kill timed out with its markers unwritten,
  followed by the log becoming writable again
- **WHEN** a retry terminates the process with its own markers written —
  or times out again with markers written
- **THEN** the outcome still reports the audit trail as incomplete and the
  page still warns — the earlier live attempt remains unaudited regardless
  of the newer attempt's marker success

#### Scenario: a kill-less retry's audit loss reaches the settlement

- **GIVEN** a pending kill whose markers were written, followed by a retry
  whose marker writes fail and whose kill call itself raises
- **WHEN** the process later dies from the earlier kill and the settlement
  seeds from the pending context
- **THEN** the stored marker state carries the retry's loss — settlement
  reports the audit chain incomplete and the page warns, instead of
  starting from the stale success

#### Scenario: a late settlement does not erase a lost audit trail

- **GIVEN** a failed cancel whose request/failure markers could not be
  written, followed by the log becoming writable again
- **WHEN** the late settlement writes its outcome marker successfully
- **THEN** the settled result still reports the audit trail as incomplete
  and the page still warns about the missing markers

#### Scenario: a graceful exit without a terminal record is not overclaimed

- **GIVEN** a POSIX cancel whose SIGINT lands before the orchestrator's
  terminal-record path is armed, with the process exiting inside the grace
  window
- **WHEN** the page presents the outcome
- **THEN** it does not claim the orchestrator wrote a terminal record —
  the claim requires a verified `finished` record whose writer pid matches
  the killed handle — and any orphaned `running` record the run left is
  adopted and corrected exactly like a forcible cancel's

#### Scenario: a pending cancellation settles without operator interaction

- **GIVEN** a failed cancel left pending context while the status signature
  and log progress stay frozen
- **WHEN** the retained handle exits during the page's polling
- **THEN** the watcher triggers the settlement automatically and the page
  re-renders with the corrected state — no manual click is required

#### Scenario: a retry racing the late death still settles

- **GIVEN** a failed cancel whose kill was issued and whose pending context
  stands, with the operator retrying while the process is exiting — the
  death landing after the page's top settlement check but before the
  retry's initial poll
- **WHEN** the retry returns "already finished"
- **THEN** the attempt routes through the full late settlement — outcome
  marker, swap diagnosis, evidence adoption — instead of the no-op path
  that would discard the handle and pending context unsettled

#### Scenario: a dead pending handle does not trap the page in re-renders

- **GIVEN** a pending handle that has exited, with the watcher fragment
  running inline on every full page execution
- **WHEN** the page executes
- **THEN** the settlement block — placed before the fragment registers —
  settles and clears the pending state first, the fragment never observes
  a dead pending handle on a full pass, and exactly one corrective
  re-render follows instead of an endless loop

#### Scenario: the scheduler's run is out of reach

- **GIVEN** only the scheduler's automatic update is running
- **WHEN** the operator looks at the run center
- **THEN** no cancel control is offered — the UI holds no handle for a
  process it did not launch
