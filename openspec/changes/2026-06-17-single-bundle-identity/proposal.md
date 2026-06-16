# Proposal: single-bundle-identity (PR-G+I)

## Why

The audit (D1) found the bundle-identity plumbing degenerate: three disjoint
sidecar formats and no single source of truth.

- `_fetch_integrity.json` IS written on the build path (`QlibBinBuilder`) but
  carried ONLY holey-fetch provenance — no content identity.
- `bundle_manifest.json` carried `tail_date`/`content_hash`/`instrument_count`,
  but its only writer (`save_manifest`) has **zero production callers** — nothing
  emits it on the build path. So its readers degenerate:
  - the feature-cache key (`read_bundle_tag`) returns `"unknown"` on every real
    bundle → a re-ingest that fixes data on the same path can still serve a
    **stale cached dataset**;
  - the walk-forward freshness check (`validate_test_end_against_bundle`) is a
    runtime **no-op** (`load_manifest` → None);
- the UI bundle-health banner reads a third never-written pair
  (`validation.json`/`manifest.json`);
- the walk-forward **resume fingerprint** has NO bundle-identity input at all, so
  a same-window re-ingest lets resume reuse folds built against the old bundle.

## What Changes

Fold a single bundle **identity** into the EXISTING `_fetch_integrity.json`
writer (the one sidecar on the build path) and re-point every consumer at it.

- **`bundle_integrity.py`**: add an OPTIONAL `BundleIdentity`
  `{tail_date, content_hash, instrument_count, calendar_start, calendar_end}`.
  `schema_version` stays **1** (identity is an optional key) so pre-existing v1
  stamps — and the `daily_recommend` gate that reads them — keep working without
  a forced rebuild. `content_hash` is sha256 of `calendars/day.txt` only (same
  scope as before; a bundle-version key, not a full-bin integrity guarantee).
- **`qlib_bin_builder.py`**: compute the identity from the STAGING bytes (after
  the calendar is written, before the atomic swap) and stamp it — promoted
  atomically with the bins.
- **`read_bundle_tag`** (feature-cache key): read the `_fetch_integrity` identity
  FIRST (canonical), RECOMPUTING the content_hash from the live calendar bytes
  (so an out-of-band edit still invalidates), falling through to the legacy
  sources for pre-PR-G+I bundles.
- **WF freshness** (`validate_test_end_against_bundle` / `verify_content_hash`):
  a new `_resolve_bundle_freshness` prefers the `_fetch_integrity` identity and
  falls back to `bundle_manifest.json`; the `QLIB_SKIP_BUNDLE_VALIDATION` bypass
  is preserved.
- **resume fingerprint** (`compute_config_fingerprint`): the engine resolves the
  SAME tag `read_bundle_tag` produces and folds it in — so cache and resume
  invalidate in lockstep. A `None`/`"unknown"` tag is NOT folded in, so adoption
  forces no re-run on identity-less bundles.
- **UI banner** (`training_guards.inspect_provider_metadata`): prefer the
  `_fetch_integrity` identity for `tail_date` + `instrument_count`.

## Impact

- A rebuilt bundle (PR-G+I on the build path) gets a non-`"unknown"` identity, so
  the feature cache, WF freshness check, resume fingerprint, and UI banner all
  light up from one source. **A bundle must be rebuilt to gain identity** —
  existing bundles keep working (degenerate-but-safe) until then.
- First WF run against an identity-bearing bundle re-runs all folds once
  (fingerprint gains the identity input); identity-less bundles are unaffected.
- `bundle_manifest.json` (`save_manifest`) is left in place as a legacy fallback
  reader; retiring the dead writer is a follow-up, not this PR.

## Non-Goals

- No new sidecar; no `schema_version` bump.
- Not adding new WF-freshness CALL SITES — the check still fires only on
  `run_walk_forward.py` (this PR makes that existing call site actually work).
  Covering UI-launched WF jobs is a follow-up.
- Not changing the `content_hash` SCOPE (calendar-only) — out-of-bin edits that
  leave the calendar unchanged remain undetected, as before.
