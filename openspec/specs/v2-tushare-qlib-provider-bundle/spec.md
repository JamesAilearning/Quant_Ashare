# v2-tushare-qlib-provider-bundle Specification

## Purpose
TBD - created by archiving change add-tushare-qlib-provider-bundle. Update Purpose after archive.
## Requirements
### Requirement: The bundle SHALL carry a single content identity on the build path

The bundle build SHALL stamp a content identity (tail date, content hash,
instrument count, calendar span) into the one sidecar it already writes on the
build path (`_fetch_integrity.json`), promoted atomically with the bins. That
identity SHALL be the single source consumed by the feature-cache key, the
walk-forward freshness check, the walk-forward resume fingerprint, and the UI
bundle-health banner — so a bundle re-ingest invalidates the feature cache and
the resume fingerprint in lockstep, and the freshness check actually fires.

The identity SHALL be added WITHOUT bumping the stamp's schema version: a bundle
built before this change (no identity block) MUST still read cleanly and MUST NOT
break the recommend gate. The feature-cache tag SHALL recompute the content hash
from the live calendar bytes (not trust the stored build-time value) so an
out-of-band calendar edit still invalidates the cache.

#### Scenario: a rebuilt bundle yields a non-unknown identity that consumers share
- **WHEN** `read_bundle_tag` and the resume fingerprint read a bundle stamped
  with identity on the build path
- **THEN** the tag is `"<tail_date>@<content_hash>"` (not `"unknown"`) and the
  resume fingerprint folds in that same tag

#### Scenario: a same-window re-ingest invalidates cache and resume together
- **WHEN** the bundle is re-ingested with the same date window but different
  calendar bytes (new content hash)
- **THEN** both the feature-cache tag and the resume fingerprint change

#### Scenario: a pre-identity bundle keeps working
- **WHEN** a bundle built before this change (stamp without an identity block) is
  read
- **THEN** the stamp parses cleanly (identity is None), the recommend gate is
  unaffected, and the resume fingerprint is byte-identical to before (no forced
  re-run)

