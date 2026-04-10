# Add universe + taxonomy artifact loader and publisher (symmetric to benchmark)

## Context

V2 currently has three data contracts — `benchmark`, `universe`,
`taxonomy` — all refactored onto `_shared_validators`. However only
`benchmark` has runtime-level producer/consumer modules
(`BenchmarkArtifactLoader` + `BenchmarkArtifactPublisher` +
`QlibTradingCalendar` injection). The other two contracts remain
contract-only: there is no code path that reads a universe csv into a
`UniverseArtifactProfile` nor any code path that produces such a csv
from operator-supplied rows.

This asymmetry is visible in two concrete ways:

1. The `_shared_validators` refactor's payoff is only tangible inside
   benchmark's e2e tests. Universe and taxonomy contracts are exercised
   by hand-built profiles inside their unit tests, so the validators'
   call-order discipline is never re-confirmed against real artifacts.
2. Operators cannot publish a universe or taxonomy artifact the way
   they can publish a benchmark artifact. Anything that needs to feed
   the runtime with membership or industry data would have to write
   one-off csv+manifest plumbing.

## Goals

1. Add `UniverseArtifactLoader` that reads csv + manifest and produces
   a `UniverseArtifactProfile` consumable by `UniverseDataContract`
   without modification. Schema is **temporal-mode aware**:
   - `static`: columns `(instrument, in_universe)`
   - `trade_date`: columns `(instrument, in_universe, trade_date)`
   - `range`: columns `(instrument, in_universe, effective_start, effective_end)`
2. Add `UniverseArtifactPublisher` that takes caller-supplied rows +
   provenance metadata, writes the canonical csv + manifest shape, and
   delegates profile construction to `UniverseArtifactLoader` so the
   producer / consumer share one code path.
3. Add the symmetric pair for taxonomy: `TaxonomyArtifactLoader` and
   `TaxonomyArtifactPublisher`, with base columns
   `(instrument, industry_code)` instead of `(instrument, in_universe)`.
4. Propagate the two hard-won benchmark-PR invariants to the new modules:
   - Strict ISO date validation on operator-facing string inputs before
     any IO occurs.
   - `snapshot_at` strict-equality against the actual data's max date
     (only in `trade_date` mode, where this is well-defined).
5. Keep four new module specs under
   `openspec/specs/v2-{universe,taxonomy}-artifact-{loader,publisher}`
   so governance review matches benchmark's layout.

## Non-goals

- This change does NOT introduce any runtime universe-selection or
  industry-mapping semantics. `RuntimeUniverseSelectionPlaceholder` and
  `IndustryRuntimeSelectionPlaceholder` remain placeholders.
- The publishers take caller-supplied rows, not a qlib provider query.
  Unlike benchmark, there is no single canonical qlib API for universe
  or industry membership, so requiring `init_qlib_canonical` would be
  forcing a dependency that has no payoff. If a future change wires a
  specific data source (e.g. a CSMAR industry table, or a qlib
  instrument-list query for universe), it can extend these publishers.
- This change does NOT implement `coverage_ratio` accounting for
  `static` or `range` temporal modes. Only `trade_date` mode has a
  natural trading-calendar denominator; the other two modes leave
  `coverage_ratio=None`.
- This change does NOT promote `TradingCalendar` from an optional
  keyword argument to a required dependency anywhere. The
  calendar-free fallbacks in benchmark loader remain untouched.

## Design notes

### Publisher input shape

Both publishers accept an explicit `rows` sequence whose shape depends
on `temporal_mode`:

- Universe `static`:   `[(instrument, in_universe_bool), ...]`
- Universe `trade_date`: `[(instrument, in_universe_bool, trade_date_iso), ...]`
- Universe `range`: `[(instrument, in_universe_bool, effective_start_iso, effective_end_iso), ...]`
- Taxonomy mirrors universe with `industry_code: str` replacing
  `in_universe: bool`.

The publisher validates row arity per mode and raises
`UniverseArtifactPublisherError` / `TaxonomyArtifactPublisherError` on
mismatch. It does NOT accept pandas DataFrames to avoid the zoo of
shape-tolerance branches the benchmark publisher had to carry for
qlib's MultiIndex output.

### snapshot_at derivation

Only meaningful when `temporal_mode == trade_date`:

- Default: publisher sets `snapshot_at = max(row.trade_date)`.
- Explicit: publisher validates
  `snapshot_at == max(row.trade_date)` and raises on mismatch, mirroring
  benchmark's strict-equality rule.

For `static` and `range` modes, the caller MUST supply `snapshot_at`
explicitly (because there is no "max row date" to derive from). The
publisher validates it as an ISO date.

### Loader future-data detection

- `static`: no date column → no future-data detection possible →
  `has_future_effective_data=False` always. Temporal leakage detection
  relies entirely on manifest `snapshot_at` vs `reference_date`.
- `trade_date`: any `trade_date > reference_date` sets
  `has_future_effective_data=True`.
- `range`: any `effective_end > reference_date` sets
  `has_future_effective_data=True`.

### Calendar injection

- Universe loader accepts optional `calendar: Optional[TradingCalendar] = None`.
  Used only in `trade_date` mode: `coverage_ratio` denominator is
  `calendar.count_trading_days(snapshot_start, snapshot_end)` if
  supplied, else `coverage_ratio=None`.
- Taxonomy loader mirrors the universe loader.

No legacy 0.63 fallback is introduced. The rationale is that universe
and taxonomy artifacts are fundamentally structural (membership /
mapping) rather than time-series price data, so an approximate
coverage-ratio fallback would be misleading. Explicit calendar
injection or explicit "coverage unknown" is preferable.
