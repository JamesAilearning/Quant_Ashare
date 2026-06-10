# Proposal: gate-downstream-on-holey-fetch

## Why

P3-4b records holes in `fetch_manifest.json` and exits non-zero, but NOTHING
downstream refuses to USE a holey (or missing) fetch. So incomplete raw data can
still silently flow two boundaries deeper: the qlib bin builder would bake a
survivorship-incomplete bundle, and `daily_recommend` would rank a buy list on it
— exactly the silent-partial-into-trading failure this project guards against.

P3-4c closes both boundaries with fail-loud gates, each with its OWN explicit
override. Critically the two overrides are INDEPENDENT and do not cascade:
building a holey research/inspection bundle (`--allow-holey-fetch`) is one
decision; trading on its recommendations (`--allow-holey-recommend`) is a strictly
higher-stakes second one. A holey bundle carries a STAMP of the fact; it never
carries the authorization. Forcing a second explicit opt-in at the trade boundary
is the deliberate caution this project wants.

## What Changes

- New `src/data/pit/bundle_integrity.py` — the build → recommend contract:
  `BundleIntegrity` + `write_bundle_integrity` (atomic) + `read_bundle_integrity`
  (missing → `None`; malformed / non-object / non-UTF-8 / unknown-schema /
  missing-field / wrong-field-type / internally-inconsistent (a clean stamp that
  lists holes) → `BundleIntegrityError`). The stamp lives at
  `{bundle}/_fetch_integrity.json` (`built_from_holey_fetch` + the fetch holes).
  Layer 1 also requires the manifest to record the bundle's endpoints, not just
  absence of holes, and surfaces a CORRUPT manifest as a `QlibBinBuilderError`
  (not an escaping `FetchManifestError`); Layer 2 normalizes `provider_uri` the
  same way qlib does before reading the stamp, and fails loud on a corrupt stamp
  even under the override.
- `src/data/tushare/fetch_manifest.py`: `all_holes(m)` + `is_complete(m)` +
  `covered_endpoints(m)` helpers.
- **Layer 1 (build gate)** — `QlibBinBuilder` (`scripts/data_pipeline/05_build_qlib_bins.py`):
  `build()` reads the fetch manifest from `--tushare-dir`. A HOLEY or MISSING
  manifest (the latter consistent with P3-4b invalidating it on a hard abort)
  means incomplete raw data → refuse with `QlibBinBuilderError` unless
  `allow_holey_fetch` (`--allow-holey-fetch`). Either way the bundle is stamped
  with its fetch integrity (clean, or built-from-holey-fetch + the holes).
- **Layer 2 (recommend gate)** — `daily_recommend.recommend()`, right after the
  staleness guard: read the bundle's integrity stamp. A holey stamp, or a MISSING
  stamp (cannot confirm completeness, e.g. a pre-P3-4c bundle), → refuse with
  `DailyRecommendationError` unless `allow_holey_recommend` (`--allow-holey-recommend`).
- The two overrides are INDEPENDENT: the stamp propagates the FACT only, never the
  authorization. A bundle built under `--allow-holey-fetch` is still refused at the
  recommend boundary unless `--allow-holey-recommend` is passed there separately.

## Non-Goals

- No bundle-build orchestration or atomic-swap of provider dirs — **P3-6**. 4c only
  defines the two gates + the minimal integrity-stamp contract; P3-6 may fold the
  stamp into a richer bundle-provenance manifest.
- No incremental / narrower-range drive — **P3-6**.
- The two overrides are NOT merged into one: build-allow never implies
  recommend-allow.
