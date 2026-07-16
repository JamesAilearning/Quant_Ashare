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
    """Return a tag identifying the bundle's contents for cache invalidation.

    Reads (in order):

    0. ``_fetch_integrity.json`` identity block (PR-G+I, the CANONICAL source —
       it is the one sidecar ``QlibBinBuilder`` actually writes on the build
       path) → returns ``"<tail_date>@<content_hash>"`` with the content_hash
       RECOMPUTED from the live ``calendars/day.txt`` (not the build-time value
       stored in the stamp), so an out-of-band calendar edit still invalidates.
       Sources 1/2 below remain as fallback for legacy bundles; note
       ``bundle_manifest.json`` (source 1) has no production writer
       (``save_manifest`` is test-only), so real bundles use source 0 or fall
       through to "unknown".
    1. ``bundle_manifest.json`` (PR #149 contract) → returns
       ``"<tail_date>@<content_hash>"`` when both fields are present
       (the ``content_hash`` field was added in PR #175), or just
       ``<tail_date>`` for legacy manifests that pre-date the hash
       opt-in. Including ``content_hash`` in the tag means a re-ingest
       that lands on the same tail_date but with different calendar
       bytes still invalidates the cache — without it, the freshness
       check (``verify_content_hash``) would correctly raise, but a
       run with ``QLIB_SKIP_BUNDLE_VALIDATION=1`` or against a soft-
       mode validator would happily return a stale dataset under the
       unchanged cache key.
    2. ``tushare_provider_manifest.json`` (existing Tushare publisher
       format that this repo's own ingest scripts emit) → returns
       ``"tushare:<coverage_end_date>@<snapshot_at>"`` so a re-ingest
       with the same coverage window still gets a distinct tag
       (``snapshot_at`` is a timestamp per-publish).

    Returns ``"unknown"`` when:

    * ``provider_uri`` is None / empty.
    * Neither manifest exists.
    * The manifest exists but can't be parsed or lacks the key fields.

    The tag is included in the cache key (see :func:`compute_cache_key`)
    so a bundle re-ingest invalidates every cached entry. Before audit
    P2, Tushare bundles always returned ``"unknown"`` because the
    reader only knew about ``bundle_manifest.json``; the resulting
    stable tag meant the cache happily served stale features across
    re-ingestions.
    """
    if not provider_uri:
        return _LEGACY_BUNDLE_TAG
    base = Path(str(provider_uri))

    # 0. PR-G+I: the build-path-written ``_fetch_integrity.json`` identity block
    #    is the CANONICAL source — it is the one sidecar QlibBinBuilder actually
    #    writes on the build path, whereas ``bundle_manifest.json`` (source 1
    #    below) has no production writer (``save_manifest`` is test-only), so on
    #    real bundles sources 1/2 fall through to "unknown". The identity tag is
    #    ``"<tail_date>@<content_hash>"`` (content_hash over calendars/day.txt),
    #    so a same-tail re-ingest with different calendar bytes still invalidates
    #    the cache. Lazy import keeps the import graph acyclic; best-effort
    #    (a malformed/legacy-without-identity stamp falls through to 1/2 → unknown).
    try:
        from src.data.pit.bundle_integrity import read_bundle_integrity
        integrity = read_bundle_integrity(base)
        if integrity is not None and integrity.identity is not None:
            tail = integrity.identity.tail_date
            # RECOMPUTE the content_hash from the live calendar bytes (do NOT
            # trust the build-time hash stored in the stamp) — same as the
            # legacy manifest branch below. The recompute is what catches an
            # out-of-band calendar edit under QLIB_SKIP_BUNDLE_VALIDATION=1 /
            # soft mode: the stored hash wouldn't move, so the cache would
            # happily serve a stale dataset for a mutated bundle.
            try:
                from src.data.bundle_manifest import compute_bundle_content_hash
                return f"{tail}@{compute_bundle_content_hash(base)}"
            except Exception:  # noqa: BLE001 — calendar unreadable
                # Cannot verify the bytes: emit a per-call unique sentinel so
                # this call cache-MISSES and no future call shares the tag
                # (mirrors the manifest branch's unverifiable-state handling).
                return f"{tail}@_calendar_unreadable_{os.urandom(8).hex()}"
    except Exception:  # noqa: BLE001 — best-effort; fall through to legacy sources
        pass

    # 1. PR #149's canonical bundle manifest.
    bundle_manifest_path = base / "bundle_manifest.json"
    if bundle_manifest_path.is_file():
        try:
            payload = json.loads(bundle_manifest_path.read_text(encoding="utf-8"))
            tail = str(payload.get("tail_date") or "").strip()
            if tail:
                # PR #175 ``content_hash`` opt-in. Decide whether the
                # bundle has opted in by looking at the FIELD VALUE
                # (non-empty string), not just key presence:
                #
                #   * Field absent OR ``null`` OR empty string =>
                #     "no integrity check requested" => preserve the
                #     legacy bare-tail tag. Critical for backwards
                #     compatibility: adopting this PR must NOT
                #     invalidate cache entries built against
                #     pre-#175 manifests, and must NOT spuriously
                #     re-hash legacy bundles where the operator
                #     never opted in. (Codex P2 follow-up on PR #175.)
                #   * Field is a non-empty string => opt-in.
                #     Recompute the SHA-256 from actual calendar bytes
                #     and use THAT in the tag (not the stored value).
                #     The recompute is what catches "someone edited
                #     the calendar out-of-band" under
                #     ``QLIB_SKIP_BUNDLE_VALIDATION=1`` or a soft-mode
                #     validator that warned-and-continued.
                content_hash_field = payload.get("content_hash")
                has_hash_opt_in = (
                    isinstance(content_hash_field, str)
                    and bool(content_hash_field.strip())
                )
                if not has_hash_opt_in:
                    return tail

                # Opt-in: try to compute from actual bytes. Lazy
                # import so the ``bundle_manifest`` <->
                # ``_feature_dataset_cache`` import graph stays acyclic
                # (a future refactor can't accidentally create a cycle).
                try:
                    from src.data.bundle_manifest import (
                        compute_bundle_content_hash,
                    )
                    actual_hash = compute_bundle_content_hash(base)
                    return f"{tail}@{actual_hash}"
                except Exception:  # noqa: BLE001 — best-effort
                    pass

                # Recompute failed (calendar missing / unreadable /
                # permission denied / TOCTOU race). The manifest
                # CLAIMS a content_hash but we cannot verify the
                # bytes match it. DO NOT fall back to the stored
                # hash: that would let the cache HIT under a tag
                # tied to a bundle state we can no longer verify
                # and silently serve a previously-built dataset for
                # what is now a broken/corrupt bundle. Instead emit
                # a per-call unique sentinel so:
                #   (i) this call cache-MISSES (no stale data served)
                #   (ii) no future call ever shares this tag (so any
                #        result we end up writing under it cannot be
                #        reused either — corrupt-state results stay
                #        ungrowing-cache-only)
                # Codex P2 follow-up on PR #175.
                return f"{tail}@_calendar_unreadable_{os.urandom(8).hex()}"
        except Exception:  # noqa: BLE001 — best-effort
            pass

    # 2. Tushare publisher's manifest (the existing format the repo's
    #    own ingest scripts have always emitted).
    tushare_manifest_path = base / "tushare_provider_manifest.json"
    if tushare_manifest_path.is_file():
        try:
            payload = json.loads(tushare_manifest_path.read_text(encoding="utf-8"))
            coverage = str(payload.get("coverage_end_date") or "").strip()
            snapshot = str(payload.get("snapshot_at") or "").strip()
            # Both fields ideally combine: coverage alone wouldn't
            # change on a re-ingest of the same window; snapshot_at
            # always does. The composite forces a fresh cache key on
            # every publish even when the window is identical.
            if coverage and snapshot:
                return f"tushare:{coverage}@{snapshot}"
            if coverage:
                return f"tushare:{coverage}"
            if snapshot:
                return f"tushare:@{snapshot}"
        except Exception:  # noqa: BLE001 — best-effort
            pass

    return _LEGACY_BUNDLE_TAG


# ---------------------------------------------------------------------------
# Cache key
# ---------------------------------------------------------------------------


def compute_cache_key(
    config: FeatureDatasetConfig,
    *,
    bundle_tag: str = _LEGACY_BUNDLE_TAG,
    handler_identity: str | None = None,
) -> str:
    """Return a stable sha256 hex digest identifying the dataset config.

    The key incorporates only the fields that affect the materialised
    dataset's contents:

    - ``instruments`` (universe)
    - ``feature_handler`` (Alpha158, MinedFactor, ...)
    - the six date split fields (train/valid/test start/end)
    - ``bundle_tag`` (e.g. the qlib provider's tail_date — covers
      bundle re-ingest)
    - ``handler_identity`` (covers handler-internal state that the
      registered name alone does not pin down — e.g. MinedFactor's
      currently-bound pool dir + PIT provider + delisted registry
      + pool parquet hash)

    Other config fields, when added in the future, MUST be included
    here if they affect dataset materialisation, or excluded if they
    only affect downstream training/backtesting.

    Audit P2: ``handler_identity`` was added because the cache key
    previously included only the handler **name**, so re-binding
    MinedFactor to a different pool produced the same key as the
    prior pool — silently serving stale features under the new pool's
    name. Callers that don't supply ``handler_identity`` fall back to
    the literal sentinel ``"_no_handler_identity_"``, which is
    intentionally consistent (no random salt) but distinct from any
    real identity string a handler could declare.
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
        "handler_identity": handler_identity or "_no_handler_identity_",
    }
    # Materialisation-affecting dimensions added AFTER the original schema join
    # the payload ONLY WHEN NON-DEFAULT: the default payload stays byte-identical
    # to the pre-dimension key, so existing cache entries remain valid, while a
    # non-default value produces a structurally distinct key (a shared entry
    # across label horizons would silently serve one horizon's labels to
    # another's training). Future dimensions follow this same pattern.
    if config.label_horizon_days != 1:
        payload["label_horizon_days"] = str(config.label_horizon_days)
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
        return None  # fallback-ok: WARNED cache-miss; caller rebuilds from source

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
        return None  # fallback-ok: cache errors degrade to a MISS; caller recomputes


__all__ = [
    "cache_get",
    "cache_path_for",
    "cache_put",
    "compute_cache_key",
    "read_bundle_tag",
]
