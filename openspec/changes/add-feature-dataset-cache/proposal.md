# Add a feature-dataset cache to FeatureDatasetBuilder

## Why

`FeatureDatasetBuilder.build()` instantiates an Alpha158 (or
MinedFactor) handler against qlib's feature store and then calls
`dataset.prepare("train", col_set="feature")` three times (train,
valid, test). On Alpha158/csi300 with 24 months of train + 3 months
valid + 3 months test, that handler instantiation alone can take
30-90 seconds (depends on bundle size + disk speed); each prepare()
adds more.

Walk-forward runs do this 8+ times — once per fold — with windows
that **mostly don't overlap**, so naive in-process memoization
doesn't help. But the same operator typically re-runs the same
config (or a config that only differs by ensemble_window or the
backtest-cost params) over and over while tuning a model. Each
re-run pays the full instantiation cost on every fold.

The user flagged "system is slowly training" in the audit. PR4 (fold
checkpoint + resume) addressed the "crash mid-run" pain; this PR
addresses the "rebuild from scratch on every config tweak" pain.

## What Changes

### Capability — opt-in pickle cache

`FeatureDatasetBuilder.build()` accepts a new optional keyword
argument `cache_dir: Path | None = None`:

- `cache_dir is None` (default) → legacy behaviour. No reads, no
  writes. This PR is fully backward-compatible: every existing test
  and every existing config keeps the same code path.

- `cache_dir is not None` → cache lookup, then build-and-store:

  1. Compute a `cache_key` = sha256 of:
     `(instruments, feature_handler, train_start, train_end,
       valid_start, valid_end, test_start, test_end, bundle_tag)`
     where `bundle_tag` is read from `bundle_manifest.json` (PR8) if
     the qlib provider directory has one; else `"unknown"`.
  2. Look for `{cache_dir}/dataset_{cache_key}.pkl`. If present and
     unpickle succeeds → return the cached `FeatureDatasetResult`
     directly. **Skips Alpha158 instantiation and all three
     prepare() calls entirely.**
  3. On cache miss (or corrupt cache file): build normally, then
     pickle the `FeatureDatasetResult` to the cache path
     atomically (`*.tmp` + `os.replace`). Write failures (disk
     full, permission) are logged at WARNING and don't abort the
     build.

### CLI / config surface

`WalkForwardConfig` gains an optional `dataset_cache_dir: str | None
= None` field. When set, `_run_single_fold` passes the directory
through to `FeatureDatasetBuilder.build()`. Operators can:

- Set per-run (`cache_dir = output_dir/.dataset_cache`) — caches
  only across folds within one run; clean dir = clean cache.
- Set shared (`cache_dir = ~/.cache/qlib_quant_v2/datasets/`) —
  caches across runs. Different config = different key = no
  collisions. **The env var `QLIB_DATASET_CACHE_DIR` overrides
  the YAML field for ad-hoc operator runs.**

## Non-Goals

- **No automatic cache eviction.** The cache grows monotonically;
  operators are responsible for cleanup (it's just a directory of
  `dataset_*.pkl` files).
- **No cross-bundle cache.** The bundle_tag in the key ensures a new
  bundle invalidates cached entries — no risk of serving stale data
  after a bundle re-ingest.
- **No dataset prepare() bypass when the handler has live qlib
  state.** The cache pickles the full `FeatureDatasetResult`
  including its qlib `DatasetH`; unpickling restores the handler's
  internal feature matrix. If qlib changes the handler's
  serialization format, the cache file becomes invalid and
  cache_get falls through to a rebuild — no silent stale-data
  failure.
- **No cache for the `pit_provider` code path.** When
  `pit_provider is not None`, the build does additional validation
  and the dataset references a live PIT provider whose state can
  drift; caching that would be unsound. The cache is bypassed
  whenever `pit_provider` is supplied.
- **No change to `register_feature_handler`** or the handler
  registry — caching is orthogonal to which handler builds the
  dataset.
- **No new dependencies.** Standard-library pickle + hashlib only.
