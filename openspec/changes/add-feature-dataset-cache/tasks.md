# Tasks: Feature Dataset Cache

## OpenSpec

- [x] Draft `proposal.md` (Why / What Changes / Non-Goals)
- [x] Draft `tasks.md`
- [x] Draft `specs/v2-feature-dataset-cache/spec.md` (3 ADDED Requirements)

## Implementation

### Cache module

- [x] `src/data/_feature_dataset_cache.py`:
      - `compute_cache_key(config)` — sha256 of normalized config + bundle_tag
      - `cache_get(cache_dir, key)` → `FeatureDatasetResult | None`
        - returns None on missing file, unpickle failure, or
          corrupted blob; logs WARNING on failure path
      - `cache_put(cache_dir, key, result)` — atomic tmp+rename
        write; WARNING on failure but never raises

### Builder integration

- [x] `FeatureDatasetBuilder.build` accepts `cache_dir: Path | None = None`
- [x] `pit_provider is not None` bypasses cache (spec non-goal)
- [x] Cache hit path returns the cached `FeatureDatasetResult`
      directly without touching the qlib handler
- [x] Cache miss path builds, then stores

### Tests

- [x] `tests/logic/test_feature_dataset_cache.py`:
      - cache key: same config → same key; different
        instruments / dates / handler → different keys
      - cache_get: missing file returns None; corrupt blob returns
        None + WARNING
      - cache_put: roundtrip; atomic (no .tmp left over); failure
        path returns None without raising
      - build with `cache_dir=None`: no reads / no writes (legacy)
      - build with `cache_dir=tmp_path`: first call writes, second
        call reads (verify by patching the build path)
      - `pit_provider` set: cache bypassed even if file would match

## Validation

- [x] `pytest tests/logic/test_feature_dataset_cache.py -q` — green
- [x] `pytest tests/logic/test_feature_dataset_builder.py -q` — no regressions
- [x] Full `tests/logic/` regression — no new failures

## Deferred (NOT this proposal)

- Automatic cache eviction policy
- Operator UI surface for cache management
- Pre-build "prefetch" CLI that warms the cache before a long
  walk-forward run
- A "verify integrity" CLI that confirms cached `dataset_*.pkl`
  files load without errors
