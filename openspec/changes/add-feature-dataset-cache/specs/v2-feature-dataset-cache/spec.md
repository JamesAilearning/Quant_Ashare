## ADDED Requirements

### Requirement: FeatureDatasetBuilder.build SHALL accept an optional cache_dir

`FeatureDatasetBuilder.build()` SHALL accept a new keyword-only parameter `cache_dir: Path | None = None`. When `cache_dir is None`, the function SHALL behave identically to its pre-cache contract — no cache reads, no cache writes, no observable change. When `cache_dir` is a `Path` (existing or not), the function SHALL attempt a cache lookup, then fall through to a normal build with a cache store.

#### Scenario: legacy callers unaffected
- **WHEN** `FeatureDatasetBuilder.build(config)` is called without `cache_dir`
- **THEN** no path is opened under any cache directory
- **AND** the result is built exactly as before

#### Scenario: pit_provider bypasses cache
- **WHEN** `FeatureDatasetBuilder.build(config, cache_dir=tmp, pit_provider=p)` is called
- **THEN** no cache lookup is attempted even if a file matching the key exists
- **AND** no cache write is attempted on the result
- **AND** the spec rationale: pit_provider has live state we cannot serialize safely

### Requirement: Cache key SHALL incorporate config fields and bundle tag

`compute_cache_key(config, *, bundle_tag)` SHALL return a stable hex digest derived from:

- `config.instruments`
- `config.feature_handler`
- `config.train_start`, `config.train_end`
- `config.valid_start`, `config.valid_end`
- `config.test_start`, `config.test_end`
- `bundle_tag` — sourced from `bundle_manifest.json` when available (PR8), else the sentinel `"unknown"`

Equal inputs SHALL produce equal keys (deterministic). Any single-field change SHALL change the key. The key SHALL be filesystem-safe (hex digest, no path separators).

#### Scenario: identical configs hash identically
- **GIVEN** two `FeatureDatasetConfig` instances with the same field values
- **WHEN** `compute_cache_key` is called on each with the same `bundle_tag`
- **THEN** both calls return the same hex digest

#### Scenario: changing train_start changes the key
- **GIVEN** two configs identical except for `train_start`
- **WHEN** keys are computed
- **THEN** the keys differ

#### Scenario: changing bundle_tag changes the key
- **GIVEN** the same config and two different bundle_tag values
- **WHEN** keys are computed
- **THEN** the keys differ — preventing serving stale data after a bundle re-ingest

### Requirement: cache_get and cache_put SHALL never propagate exceptions

`cache_get(cache_dir, key)` SHALL return `FeatureDatasetResult | None`. A missing file, a corrupt pickle, an OSError on read, or any unpickling exception SHALL return `None` (cache miss) with a WARNING log; the calling builder SHALL fall through to a fresh build.

`cache_put(cache_dir, key, result)` SHALL write atomically via `*.tmp` + `os.replace`. A disk-full error, permission error, or pickling failure SHALL log a WARNING and return `None` rather than raise — the build result has already been produced and the caller depends on it being returned.

#### Scenario: missing cache file returns None
- **WHEN** `cache_get(cache_dir, "nonexistent")` is called against a directory with no matching file
- **THEN** the call returns `None` without raising

#### Scenario: corrupt cache file returns None
- **GIVEN** `cache_dir/dataset_abc.pkl` exists but is not a valid pickle
- **WHEN** `cache_get(cache_dir, "abc")` is called
- **THEN** the call returns `None`
- **AND** a WARNING is logged

#### Scenario: cache write failure does not block result return
- **GIVEN** `cache_dir` is not writable (or disk is full)
- **WHEN** `cache_put(cache_dir, key, result)` is called
- **THEN** the call logs a WARNING and returns without raising
- **AND** the build result is still returned to the caller unchanged
