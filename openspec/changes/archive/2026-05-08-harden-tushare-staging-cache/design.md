## Overview

The staged cache must be treated as a source-data cache, not as a filtered
publish artifact. Reuse is safe only when the file was produced by the same
Tushare API call parameters, and raw daily/adjustment files remain reusable
across future wider instrument scopes only if they are never overwritten by
scope-filtered frames.

## Cache Metadata

Each staged CSV written by the generic fetch path will receive a sidecar
metadata file recording:

- Tushare API name.
- JSON-stable request parameters.
- Cache schema version.

When `reuse_staged` is true, the fetcher reads an existing CSV only if the
sidecar metadata exists and matches the current request. Missing or mismatched
metadata causes a refetch. This intentionally invalidates pre-change staged
files once, because their provenance and request parameters are unknown.

## Raw vs Filtered Frames

`daily/<trade_date>.csv` and `adj_factor/<trade_date>.csv` are raw Tushare
payloads keyed by trade date. Instrument filtering happens after reading or
fetching and feeds only the returned `TushareStagedMarketData`; it must not be
written back to the raw path. A later `instruments: all` run can therefore
reuse the broader raw file instead of inheriting a previous subset.

## Pipeline Lag Validation

`PipelineConfig.__post_init__` gets the same bool rejection already present in
`WalkForwardConfig` and `CanonicalBacktestContract`: Python bools are ints, but
operator config `true`/`false` is malformed for this field and should fail
before feature/model work begins.

## Alternatives Considered

- Scope-specific filtered cache directories: rejected for now because the
  publisher does not need persistent filtered payloads; in-memory filtering is
  simpler and keeps one raw cache per API request.
- Filename-only parameter keying: rejected because sidecar validation also
  protects existing paths and can invalidate legacy unprovenanced files.
