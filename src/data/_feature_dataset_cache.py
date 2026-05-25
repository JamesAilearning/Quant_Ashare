"""Opt-in pickle cache for ``FeatureDatasetResult``.

`FeatureDatasetBuilder.build()` calls qlib's Alpha158 (or MinedFactor)
handler and then runs three `dataset.prepare()` calls. On large
universes that's 30-90+ seconds per fold; a walk-forward with 8 folds
re-pays the full cost on every config tweak.

This module adds an opt-in cache: hash the relevant config fields
(plus the qlib bundle's manifest tag, when available), pickle the
final `FeatureDatasetResult`, and serve it on subsequent calls.

See `openspec/changes/add-feature-dataset-cache/` for the full
contract. Three-bullet design summary:

1. **Cache miss is the safe default.** Any unexpected condition
   (missing file, corrupt pickle, OSError) returns None and the
   builder falls through to a fresh build.
2. **Write failures never block return.** ``cache_put`` logs at
   WARNING and returns; the build result is already in hand.
3. **Bundle tag in the key.** A bundle re-ingest invalidates every
   cached entry by changing the hash, so we cannot accidentally
   serve stale data.

This module does not import qlib. The cached object IS a `FeatureDatasetResult`
holding a qlib `DatasetH`; the cache file's bytes come from `pickle.dump`
of that whole tree. If qlib changes its DatasetH serialization format,
the unpickle fails and we treat it as a cache miss — never a stale
hit.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import pickle
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.data.feature_dataset_builder import (
        FeatureDatasetConfig,
        FeatureDatasetResult,
    )

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Bundle tag
# ---------------------------------------------------------------------------


_LEGACY_BUNDLE_TAG = "unknown"


def read_bundle_tag(provider_uri: str | os.PathLike[str] | None) -> str:
    """Return the tail_date from the qlib provider's bundle_manifest.json.

    Returns ``"unknown"`` when:
      - ``provider_uri`` is None / empty
      - the bundle directory has no ``bundle_manifest.json`` file
      - the manifest exists but can't be parsed / lacks tail_date

    The tag is included in the cache key (see :func:`compute_cache_key`)
    so a bundle re-ingest invalidates every cached entry.
    """
    if not provider_uri:
        return _LEGACY_BUNDLE_TAG
    try:
        manifest_path = Path(str(provider_uri)) / "bundle_manifest.json"
        if not manifest_path.is_file():
            return _LEGACY_BUNDLE_TAG
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        tail = str(payload.get("tail_date") or "").strip()
        return tail or _LEGACY_BUNDLE_TAG
    except Exception:  # noqa: BLE001 — best-effort
        return _LEGACY_BUNDLE_TAG


# ---------------------------------------------------------------------------
# Cache key
# ---------------------------------------------------------------------------


def compute_cache_key(
    config: FeatureDatasetConfig,
    *,
    bundle_tag: str = _LEGACY_BUNDLE_TAG,
) -> str:
    """Return a stable sha256 hex digest identifying the dataset config.

    The key incorporates only the fields that affect the materialised
    dataset's contents:

    - ``instruments`` (universe)
    - ``feature_handler`` (Alpha158, MinedFactor, ...)
    - the six date split fields (train/valid/test start/end)
    - ``bundle_tag`` (e.g. the qlib provider's tail_date)

    Other config fields, when added in the future, MUST be included
    here if they affect dataset materialisation, or excluded if they
    only affect downstream training/backtesting.
    """
    payload = {
        "instruments": config.instruments,
        "feature_handler": config.feature_handler,
        "train_start": config.train_start,
        "train_end": config.train_end,
        "valid_start": config.valid_start,
        "valid_end": config.valid_end,
        "test_start": config.test_start,
        "test_end": config.test_end,
        "bundle_tag": bundle_tag,
    }
    encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:32]


def cache_path_for(cache_dir: Path | str, key: str) -> Path:
    """Return the on-disk path for a cache key."""
    return Path(cache_dir) / f"dataset_{key}.pkl"


# ---------------------------------------------------------------------------
# Read / write
# ---------------------------------------------------------------------------


def cache_get(
    cache_dir: Path | str | None,
    key: str,
) -> FeatureDatasetResult | None:
    """Return the cached ``FeatureDatasetResult`` for ``key``, or None.

    Any condition that prevents a clean load (missing file, corrupt
    pickle, OSError, unexpected unpickle type) returns None with a
    WARNING log — the caller falls through to a fresh build.
    """
    if cache_dir is None:
        return None
    path = cache_path_for(cache_dir, key)
    if not path.is_file():
        return None
    try:
        with path.open("rb") as f:
            result = pickle.load(f)
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "feature-dataset cache at %s could not be loaded "
            "(%s: %s); treating as cache miss and rebuilding.",
            path, type(exc).__name__, exc,
        )
        return None

    # Late import to avoid a circular import at module load.
    from src.data.feature_dataset_builder import FeatureDatasetResult  # noqa: PLC0415

    if not isinstance(result, FeatureDatasetResult):
        _log.warning(
            "feature-dataset cache at %s contains %s, not "
            "FeatureDatasetResult; treating as cache miss.",
            path, type(result).__name__,
        )
        return None
    return result


def cache_put(
    cache_dir: Path | str | None,
    key: str,
    result: FeatureDatasetResult,
) -> Path | None:
    """Persist ``result`` under ``cache_dir`` keyed by ``key``.

    Atomic via ``*.tmp`` + ``os.replace``. Any write failure (disk
    full, permission denied, pickle error) logs a WARNING and returns
    None rather than raising — the build result has already been
    produced and the caller depends on it being returned.

    Returns the resolved cache path on success, None on failure.
    """
    if cache_dir is None:
        return None
    try:
        d = Path(cache_dir)
        d.mkdir(parents=True, exist_ok=True)
        target = cache_path_for(d, key)
        tmp = target.with_suffix(target.suffix + ".tmp")
        with tmp.open("wb") as f:
            pickle.dump(result, f, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, target)
        return target
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "feature-dataset cache write to %s failed (%s: %s); "
            "build result is still returned to caller unchanged.",
            cache_dir, type(exc).__name__, exc,
        )
        # Best-effort: try to remove any leftover .tmp so the next
        # write doesn't trip on it. Swallow secondary errors.
        try:
            Path(cache_dir, f"dataset_{key}.pkl.tmp").unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
        return None


__all__ = [
    "cache_get",
    "cache_path_for",
    "cache_put",
    "compute_cache_key",
    "read_bundle_tag",
]
