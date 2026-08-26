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
leave no marker and change nothing. When a marker write fails (the log
became unwritable after launch), the termination itself still proceeds —
but the outcome SHALL report the missing audit trail and the page SHALL
warn loudly rather than letting the operator action go unrecorded in
silence.

The forcible-cancel presentation SHALL be conditioned on what the post-kill
reread actually found: only when a matching `running` record was found and
the evidence persisted may the page claim the artifact stays `running`,
will remain labelled cancelled, and that the gate unlocked. A kill landing
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
  coerce it.
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
superseded — and the launch gate SHALL unlock **all the way through the
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
record blocking launches. The late adoption's time bound SHALL be the
death-observation moment sampled at the settlement boundary's entry
(before the status reread, same sample-then-read discipline as the
immediate path) — NOT the request moment: the child can write its
`running` record after the request was captured but before the kill takes
effect, and a request-time bound would reject that genuine orphan and
leave it blocking launches to the staleness threshold. With the process
identity as the primary discriminator, the window's only remaining job is
pid-reuse defence, which any bound at or after the real death serves.

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
after a kill call that itself raised the process was never touched and its
later natural completion SHALL retire as an ordinary self-completion, never
be settled (and marked, and swap-diagnosed) as a forcible cancel that did
not happen. The POSIX pre-signals are sent with errors suppressed, so their
delivery is unprovable — that path SHALL count as not-issued (fail-closed:
an orphan waiting out the staleness threshold beats labelling a natural
completion as killed). The pending context SHALL also carry the failed
attempt's marker-audit state, and late settlement SHALL aggregate it rather
than reset to the optimistic default: when the request/failure markers never
reached the log, a successfully written late outcome marker does not make
the audit trail complete, and the settled result SHALL keep reporting the
gap.

A graceful outcome's claim that the orchestrator wrote its own terminal
record SHALL be VERIFIED, never inferred from timely process death: the
polite signal can land while the orchestrator is still importing modules,
parsing configuration, or acquiring the single-flight lock — before its
terminal-record path is armed — and the process still exits within the
grace window while the artifact stays missing, stale, or still `running`.
Verification SHALL require the status artifact to show a `finished` record
whose writer pid equals the killed handle's pid (the same process identity
the adoption uses) AND whose `started_at`/`finished_at` both fall inside
the session's launch-to-exit window — pid alone would let a launch that
reuses the pid stored in an OLDER finished artifact verify that stale
artifact as this run's terminal record when the signal kills the new child
before it writes anything; anything else — a running record, a pid-less
record, out-of-window or unparseable or zone-naive stamps, a missing or
corrupt artifact, an unverifiable provider, an absent window bound — SHALL
read as not-confirmed. A graceful exit WITHOUT a confirmed terminal record SHALL
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

#### Scenario: a record written after the cancel request still settles

- **GIVEN** a cancel confirmed in the window after the spawn but before the
  child wrote its `running` record, whose kill times out while the child
  writes that record, with the death arriving late
- **WHEN** the pending settlement binds the evidence
- **THEN** the record — written after the request moment but inside the
  launch-to-observed-death window with the killed pid — is adopted, and
  the orphan does not block launches to the staleness threshold

#### Scenario: a stale terminal artifact with a reused pid is not verified

- **GIVEN** a launch whose child received the same pid stored in an older
  `finished` artifact and was killed before writing any status
- **WHEN** the graceful outcome verifies the terminal record
- **THEN** verification fails on the time window — the old artifact's
  stamps predate this session's launch — and the page does not claim the
  orchestrator wrote a terminal record

#### Scenario: a failed kill call does not turn a natural finish into a cancel

- **GIVEN** a cancel attempt whose kill call itself raised, leaving the
  process untouched
- **WHEN** that process later finishes naturally and the page retires the
  handle
- **THEN** no late settlement runs — no cancel outcome, no late-exit
  marker, no swap diagnosis — and the run's own status artifact speaks
  for its completion

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

#### Scenario: the scheduler's run is out of reach

- **GIVEN** only the scheduler's automatic update is running
- **WHEN** the operator looks at the run center
- **THEN** no cancel control is offered — the UI holds no handle for a
  process it did not launch
