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

Walk-forward fold reports already carry the canonical backtest provenance that
establishes execution semantics, ST-mask identity, and runtime/bundle identity.
At aggregate write time, the engine publishes that evidence only when every
persisted fold report contains the same required values.  A missing artifact is
recorded as `unavailable`, and any disagreement as `mixed`; neither status is
eligible for controlled ordering.  The engine also pins both bundle identities
at run start and rechecks them before and after each non-resumed fold, refusing
to publish a cross-generation aggregate if the provider changes mid-run.
Pipeline and walk-forward reports share the same top-level
`comparison_provenance` schema: Pipeline resolves its one canonical backtest
record, while Walk-Forward resolves all fold records.

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
