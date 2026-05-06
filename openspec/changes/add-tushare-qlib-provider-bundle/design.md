## Context

The V2 runtime already has a single canonical qlib initialization boundary and
uses qlib provider data for feature generation, training, and official
backtests. The repository also has a Tushare integration, but its current scope
is limited to Shenwan industry taxonomy artifacts. Operators who want to test
Tushare as an OHLCV source need a reproducible bridge into qlib's provider
format before the training pipeline can consume that data safely.

Tushare daily bars are unadjusted, adjustment factors are exposed separately,
and account permissions / rate limits vary by token. The bridge therefore needs
to make source APIs, adjustment mode, coverage, and validation results explicit
rather than treating Tushare as a drop-in replacement.

## Goals / Non-Goals

**Goals:**

- Build an opt-in Tushare-to-qlib provider bundle publisher for A-share daily
  OHLCV data.
- Include explicitly configured benchmark index daily bars in the generated
  provider bundle so canonical backtests can read the configured benchmark from
  the same opt-in provider path.
- Record manifest/provenance metadata sufficient to audit source APIs, coverage,
  adjustment mode, package version, and validation health.
- Validate staged Tushare data before publishing a bundle that can be used as a
  qlib `provider_uri`.
- Preserve the existing canonical training/backtest path unless the operator
  explicitly points config at the generated provider bundle.
- Provide comparison hooks so maintainers can evaluate Tushare data against the
  existing qlib provider before making any future default-source decision.

**Non-Goals:**

- No automatic switch from the current qlib bundle to Tushare data.
- No minute-level data, fundamentals, moneyflow, realtime data, or intraday
  training features in this change.
- No live network calls from `src/core/` or canonical runtime modules.
- No Tushare token storage in committed YAML, manifests, logs, or run artifacts.
- No guarantee that Tushare is "better" than the current data source without a
  later A/B comparison and approved source-of-truth decision.

## Decisions

1. **Publish a qlib provider bundle, not a direct runtime adapter.**

   The publisher will run under `src/data/tushare/` and produce files that qlib
   can consume through the existing `provider_uri` path. This keeps network I/O
   and vendor-specific logic out of `src/core/`. Alternative considered: a qlib
   data adapter that calls Tushare at runtime. That would couple official
   training/backtest behavior to network availability and token permissions, so
   it is rejected for canonical use.

2. **Use Tushare `daily` plus `adj_factor` instead of `pro_bar` as the first
   source path.**

   Pulling raw daily bars and factors separately lets the project compute and
   record the chosen adjustment convention itself. Alternative considered:
   relying on `pro_bar` adjusted output. That is simpler, but it hides more of
   the adjustment semantics inside a vendor helper and makes reproducibility
   harder to inspect.

3. **Require an explicit output adjustment mode.**

   The publisher config will require one supported `data_adjust_mode` and write
   the same value into the manifest. The generated bundle must be passed to
   canonical qlib init with a matching provider adjustment mode. This follows
   the existing canonical runtime boundary rather than adding a second meaning
   for adjusted data.

4. **Stage raw pulls before publishing.**

   The workflow will stage raw Tushare payloads and a validation profile before
   writing or replacing the final provider bundle. A failed validation leaves
   existing bundles untouched. Alternative considered: stream API responses
   directly into the qlib destination. That gives less auditability and worse
   rollback behavior.

5. **Treat comparison against the current provider as informational.**

   The first implementation should emit row-count, coverage, overlap, and price
   difference summaries when a baseline qlib provider is supplied, but it must
   not hard-code a policy that Tushare is superior or inferior. A future change
   can define source-of-truth promotion criteria after real comparison results
   exist.

6. **Write benchmark features without adding them to the stock universe.**

   The publisher can fetch configured Tushare `index_daily` series, such as
   HS300, and write qlib feature files under index-style codes like
   `SH000300`. These benchmark rows are recorded in the manifest and validation
   profile, but are not written into `instruments/all.txt`; the training
   universe remains stock-only unless an operator explicitly provides another
   instrument file.

## Risks / Trade-offs

- **Tushare permission or rate-limit failures** -> keep all Tushare calls behind
  the optional extra and typed client errors; support resumable staging so a
  partial pull does not corrupt a published bundle.
- **Adjustment convention mismatch** -> require explicit `data_adjust_mode`,
  record factor coverage, and add validation that rejects missing factors for
  adjusted output.
- **Silent survivorship bias** -> fetch stock metadata and trading calendar
  separately, record instrument coverage, and report gaps instead of silently
  dropping symbols.
- **Large historical pulls are slow** -> support date/instrument ranges,
  staged files, and deterministic re-runs before optimizing for parallelism.
- **qlib binary format details vary by version** -> isolate qlib dump/conversion
  behind one publisher module and test it with a small fixture bundle.

## Migration Plan

1. Add contract and publisher code without changing default `config.yaml`
   `provider_uri`.
2. Add an example Tushare provider config and CLI that writes to a separate
   output directory, such as `output/qlib_tushare/`.
3. Validate the generated bundle and run a smoke training job only when the
   operator explicitly points `provider_uri` to that output.
4. Rollback is deleting or ignoring the generated bundle and returning
   `provider_uri` to the previous qlib data directory.

## Open Questions

- Which historical start date should be recommended for the first full A-share
  training bundle?
- Which benchmark index set beyond HS300 should be recommended in the example
  config?
- What tolerance should future A/B comparison use for adjusted price differences
  between the existing provider and the Tushare-generated provider?
