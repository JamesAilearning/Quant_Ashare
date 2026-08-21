## Context

The operator console currently renders one research run at a time.  That is
appropriate for inspection, but it gives no evidence that two displayed
numbers share the same experiment contract before an operator compares them.
The comparison workbench must therefore make comparability explicit rather
than reconstructing metrics or silently treating incomplete artifacts as
equivalent.

## Goals / Non-Goals

**Goals:**

- Let an operator select two to five historical pipeline or walk-forward runs
  and restore that selection from the URL.
- Read only the existing job catalog, run configuration, and report artifacts.
- Check the experiment contract before presenting a controlled ordering.
- Present existing result metrics, walk-forward evidence, provenance, and
  exact deep links without implying serving eligibility.

**Non-Goals:**

- Launching, cancelling, changing, or deleting a run.
- Recomputing metrics, folding results, filling in missing provenance, or
  deriving a new official result.
- Promoting a research result to production or altering trading execution.

## Decisions

### Read model and artifact boundary

The page will use the existing unified job catalog as its selector source and
will read only the selected runs' `config.yaml`, `pipeline_report.json`, or
`walk_forward_report.json` through the existing guarded artifact-reader
boundary.  The read model is pure after artifact loading so contract and
ranking decisions are directly testable.

For pipeline runs, the page accepts only the complete, resolved
`PipelineConfig` serialization written by the pipeline producer.  It does not
fill omitted keys from current defaults.  The UI runner keeps its submitted
input in the job directory and must not overwrite the complete run artifact;
legacy or incomplete artifacts remain visible but block ordering.

For walk-forward runs, `walk_forward_report.json["config"]` is the only
configuration evidence used for the comparison contract.  The aggregate writer
serializes the exact `WalkForwardConfig` there, so the page requires the exact
field set and re-applies that configuration's value validation without filling
defaults or using a sidecar input file.  It also cross-checks every producer
field shared with the consistent fold-derived `comparison_provenance.config`:
benchmark, execution lag, account cash, ST-mask input path, adjustment mode,
exchange/cost controls, and the runtime adjustment mode.  A source-level null
stamp-tax schedule is compared as the canonical default against the
producer-expanded schedule; a disagreement or missing report-side expansion
blocks ordering.  Likewise, every shared field must appear in folded
provenance, including an explicit null ST-mask path for a disabled input; an
omission is inconsistent evidence, not an invitation to infer equality.
The projection includes only the ST input path and digest: each fold's
`n_st_masked` is an outcome count that naturally changes with the fold window,
so it must not turn stable input evidence into a mixed comparison contract.
An unknown ST-provenance field makes comparison evidence unavailable rather
than being silently classified as stable.
The selected catalog row's producer-written `config_fingerprint` is carried
through only as a read-only configuration identity for display because the
walk-forward aggregate has no corresponding top-level backtest-provenance
field. It never substitutes for configuration-contract evidence or permits
controlled ordering; an absent catalog value remains visibly unavailable.

When a UI lifecycle row and a CLI catalog row share an artifact directory, the
page aliases them only if the CLI producer wrote that UI job ID into its catalog
record.  Timestamp overlap is lifecycle context rather than identity, so an
unlinked CLI record remains the owner of the current artifacts.

### Comparison contract

The gate compares the fields that change an experiment's meaning: universe,
training/validation/testing windows, benchmark, execution lag, canonical cost
and exchange controls, and data provenance (the canonical runtime snapshot,
the calendar content tag, and the producer-written bundle rebuild identity in
backtest provenance and feature-cache keys).  A value absent,
malformed, or different in any selected run is a block, with the affected run
IDs and fields shown to the operator.

The page does not infer a value from a sibling field.  It displays unavailable
values as unavailable and explains why comparison is blocked.
The artifact reader translates UTF-8 and JSON value-decoding failures into
displayable read issues, so a damaged report remains unavailable evidence
rather than aborting page rendering. An `official` metric status is accepted
only when its producer-written `metrics_purpose` is explicitly `official`;
missing or unsupported purposes remain unverified.

Walk-forward fold reports already carry the canonical backtest provenance that
establishes the official canonical path, execution semantics, ST-mask identity,
and runtime/bundle identity.  At aggregate write time, the engine publishes
that evidence only when every persisted fold report contains the same required
values.  A missing artifact is recorded as `unavailable`, and any disagreement
as `mixed`; neither status is eligible for controlled ordering.  The engine
also pins both bundle identities at run start and rechecks them before and
after each non-resumed fold, refusing to publish a cross-generation aggregate
if the provider changes mid-run.

Unreadable fold-report evidence, including text-decode and JSON-value failures,
is likewise recorded as unavailable rather than aborting aggregate publication.
The read model accepts reported fold stability only when the declared valid-IR
count equals the finite IR values in uniquely indexed fold records and the
producer-recorded mean and population standard deviation equal those finite
values.  This is an integrity cross-check only: the page never substitutes a
recalculated value for display or ranking when the recorded aggregate differs.
It also validates the producer-recorded aggregate metric status from the
serialized fold status and prediction-shape evidence: measured official folds
are required for an official aggregate, a predictions-only declaration
downgrades the aggregate, and a `[0]` failed placeholder is correctly treated
as no measurement. Missing or contradictory status evidence blocks controlled
ordering rather than allowing an aggregate label to launder it.

An explicitly stamped `st_mask_mode="off_experiment"` represents the stable
research identity `off_experiment` only when the folded backtest provenance
contains exactly the producer's no-input `st_mask.namechange_path` (null or
blank) and no content digest.  ST-on runs continue to require the recorded
content digest.  A path or digest alongside the off declaration is
contradictory evidence, not an implicit fallback to either mode.

Pipeline and walk-forward reports share the same top-level
`comparison_provenance` schema: Pipeline resolves its one canonical backtest
record, while Walk-Forward resolves all fold records.

Pipeline captures both provider identities immediately after canonical runtime
initialization and before feature construction. It supplies that captured pair
to canonical backtest provenance, then rechecks the live pair before the
backtest and immediately before report publication. A changed calendar-content
or rebuild identity aborts the run rather than publishing a report that binds
model or prediction bytes from one generation to provenance from another.

For pipeline rows, the read model also compares each complete resolved
`config.yaml` with the producer-written overlapping report and canonical
backtest fields. Runtime provider paths and regions are compared after the
canonical runtime normalizes them; a source-level `null` stamp-tax schedule is
resolved to the canonical default and compared with the producer-written
expanded request. For official metrics, the top-level, nested backtest, and
comparison-projection canonical paths must also agree. A mismatch blocks
ordering; configuration files are display
references only when they can be tied to the report that supplied the metrics.

### Controlled ordering

Only a compatible, artifact-complete selection can receive a stable ordering
by the existing reported information ratio.  The page labels it as a
read-only research ordering, preserves the source metric's `扣费后超额` meaning,
and never calls it a production recommendation.  Non-comparable runs remain
visible, but no rank is assigned.

### Navigation

The page keeps selected IDs in one validated `run_ids` URL parameter.  It
links each exact run ID to Results, and walk-forward runs to Walk Forward.  It
also exposes the exact configuration and log artifact paths as read-only
references, rather than inventing a mutation-capable configuration route.

## Risks / Trade-offs

- Older artifacts may lack data provenance.  Blocking their ordering is more
  conservative than displaying a potentially invalid comparison.
- Reports from the two engines have different metric layouts.  The read model
  labels their source and only displays values actually written by each report.
- A shared URL can name deleted or inaccessible runs.  The page removes no
  evidence silently: it displays an explicit unavailable/unknown-run issue.

## Migration Plan

This is an additive, read-only page.  Existing catalog and detail-page URLs
remain unchanged.  No data migration, artifact rewrite, or rollback action is
needed; removing the new navigation entry and page returns the console to its
previous behavior.

## Open Questions

None.  Missing legacy metadata is deliberately handled as a visible comparison
block, not as a request to mutate historical artifacts.
