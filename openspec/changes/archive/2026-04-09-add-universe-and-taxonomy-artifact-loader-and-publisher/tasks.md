# Tasks

## 1. Universe artifact loader (`src/data/universe_artifact_loader.py`)

- [x] Create `UniverseArtifactLoader` with `load(artifact_path,
      manifest_path, *, temporal_mode, reference_date=None,
      calendar=None) -> UniverseArtifactProfile`.
- [x] Raise `UniverseArtifactLoaderError` on missing path arguments or
      on an unknown `temporal_mode`.
- [x] Produce a valid profile with `artifact_present=False` when the
      csv file is absent — data-level issues must surface via contract
      codes, not loader exceptions.
- [x] Parse manifest json; treat corrupt manifest as empty dict
      (existing benchmark pattern).
- [x] Per-mode csv reading:
  - `static`: no date columns; rows counted, columns normalised.
  - `trade_date`: parse `trade_date` per row; track min/max; set
    `has_future_effective_data` if any `trade_date > reference_date`.
  - `range`: parse `effective_start`/`effective_end`; track
    `min(effective_start)` / `max(effective_end)`; set
    `has_future_effective_data` if any `effective_end > reference_date`.
- [x] `coverage_ratio` computed only in `trade_date` mode with an
      injected calendar; else `None`.
- [x] `has_snapshot_at_mismatch` enforced only in `trade_date` mode:
      compare manifest `snapshot_at` to `max(trade_date)`.
- [x] `has_future_known_metadata` set when manifest `snapshot_at >
      reference_date` (same as benchmark).

## 2. Universe artifact publisher (`src/data/universe_artifact_publisher.py`)

- [x] Create `UniverseArtifactPublisher.publish(universe_name,
      temporal_mode, rows, artifact_path, manifest_path, *,
      source_name=..., source_uri=None, snapshot_at=None,
      reference_date=None, calendar=None) -> UniversePublishResult`.
- [x] Raise `UniverseArtifactPublisherError` on empty string args and
      on unknown `temporal_mode`.
- [x] Validate row arity per mode (2 / 3 / 4 element tuples).
- [x] ISO-validate any date fields present in rows BEFORE writing any
      file, raising at the boundary.
- [x] `trade_date` mode: derive default `snapshot_at = max(trade_date)`;
      strict-equality check if explicit `snapshot_at` supplied.
- [x] `static` / `range` modes: `snapshot_at` is REQUIRED from the
      caller and must be a valid ISO date; no implicit derivation.
- [x] Refuse to emit empty artifacts (`rows` empty → raise).
- [x] Write csv + manifest, then delegate profile construction to
      `UniverseArtifactLoader.load`. No direct construction of
      `UniverseArtifactProfile`.
- [x] Leave no partial files behind on validation failure — do all
      validation before any IO.

## 3. Taxonomy artifact loader (`src/data/taxonomy_artifact_loader.py`)

- [x] Mirror `UniverseArtifactLoader`. Base columns
      `(instrument, industry_code)` instead of
      `(instrument, in_universe)`.
- [x] Raise `TaxonomyArtifactLoaderError` on structural misuse.
- [x] Produce `TaxonomyArtifactProfile` consumable by
      `TaxonomyDataContract` unchanged.

## 4. Taxonomy artifact publisher (`src/data/taxonomy_artifact_publisher.py`)

- [x] Mirror `UniverseArtifactPublisher` with
      `taxonomy_name`/`industry_code` naming and
      `TaxonomyArtifactPublisherError`.

## 5. Tests

- [x] `tests/logic/test_universe_artifact_loader.py`: happy-path for
      all three temporal modes, missing artifact, missing manifest,
      snapshot_at mismatch in trade_date mode, future-dated row
      detection, calendar injection happy path.
- [x] `tests/logic/test_universe_artifact_publisher.py`: happy-path
      round trip for all three modes, bad ISO input rejected before
      IO, row arity mismatch, empty rows refused, snapshot_at mismatch
      in trade_date mode, snapshot_at required for static/range.
- [x] `tests/logic/test_taxonomy_artifact_loader.py`: mirror universe
      loader tests.
- [x] `tests/logic/test_taxonomy_artifact_publisher.py`: mirror
      universe publisher tests.

## 6. Quality gates

- [x] `python -m unittest discover -s tests` passes with full suite
      green. Test count baseline 149 increases by at least the number
      of new tests added in section 5.
- [x] `ruff check src/data/universe_artifact_loader.py src/data/universe_artifact_publisher.py src/data/taxonomy_artifact_loader.py src/data/taxonomy_artifact_publisher.py tests/logic/test_universe_artifact_loader.py tests/logic/test_universe_artifact_publisher.py tests/logic/test_taxonomy_artifact_loader.py tests/logic/test_taxonomy_artifact_publisher.py`
      (skipped if ruff is not available in env; recorded either way).

## 7. Governance

- [x] Promote the four spec deltas into
      `openspec/specs/v2-universe-artifact-loader/spec.md`,
      `openspec/specs/v2-universe-artifact-publisher/spec.md`,
      `openspec/specs/v2-taxonomy-artifact-loader/spec.md`,
      `openspec/specs/v2-taxonomy-artifact-publisher/spec.md` (new
      files).
- [x] Archive the change into
      `openspec/changes/archive/2026-04-09-add-universe-and-taxonomy-artifact-loader-and-publisher/`
      with `status: archived`.
