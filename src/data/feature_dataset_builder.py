"""Feature dataset builder — thin wrapper around qlib Alpha158 + DatasetH.

Provides a contract-friendly interface for constructing feature datasets
that downstream model trainers consume directly. All date inputs are
ISO-validated before any qlib IO.

Boundaries
----------
- This module does NOT call ``qlib.init``. Callers must initialize via
  ``src.core.qlib_runtime.init_qlib_canonical`` first.
- Importing this module does NOT import qlib. The qlib import is lazy.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.contracts._shared_validators import parse_iso_date
from src.core.qlib_runtime import is_canonical_qlib_initialized

_log = logging.getLogger(__name__)


class FeatureDatasetBuilderError(RuntimeError):
    """Raised on structural misuse or qlib failures."""


FeatureHandlerFactory = Callable[["FeatureDatasetConfig"], Any]
_FEATURE_HANDLER_REGISTRY: dict[str, FeatureHandlerFactory] = {}


@dataclass(frozen=True)
class FeatureDatasetConfig:
    """Frozen configuration for feature dataset construction."""

    instruments: str
    feature_handler: str
    train_start: str
    train_end: str
    valid_start: str
    valid_end: str
    test_start: str
    test_end: str


@dataclass(frozen=True)
class FeatureDatasetResult:
    """Result of feature dataset construction."""

    dataset: Any  # qlib DatasetH — opaque to contracts
    train_shape: tuple[int, int]
    valid_shape: tuple[int, int]
    test_shape: tuple[int, int]
    feature_columns: tuple[str, ...]


def register_feature_handler(
    name: str,
    factory: FeatureHandlerFactory,
    *,
    replace: bool = False,
) -> None:
    """Register a qlib feature handler factory by explicit name."""

    handler_name = str(name or "").strip()
    if not handler_name:
        raise FeatureDatasetBuilderError("feature handler name must be non-empty.")
    if not callable(factory):
        raise FeatureDatasetBuilderError("feature handler factory must be callable.")
    if handler_name in _FEATURE_HANDLER_REGISTRY and not replace:
        raise FeatureDatasetBuilderError(
            f"feature handler {handler_name!r} is already registered."
        )
    _FEATURE_HANDLER_REGISTRY[handler_name] = factory


def list_supported_feature_handlers() -> tuple[str, ...]:
    """Return registered feature handler names."""

    return tuple(sorted(_FEATURE_HANDLER_REGISTRY))


def _reset_feature_handler_registry_to_defaults() -> None:
    """Reset feature handler registry to default registrations.

    Used at module-import time to seed the registry with the built-in
    handlers, and by tests that register a custom factory and need to
    restore the baseline before the next test runs.

    The previous name (``_reset_feature_handler_registry_for_tests``)
    was misleading: the same call also runs at module-import (see the
    bottom of this file), so any caller who imports the module *after*
    registering a custom handler would silently lose their registration.
    The new name reflects that this is the canonical "wipe and restore
    defaults" operation, not test-only plumbing.
    """

    _FEATURE_HANDLER_REGISTRY.clear()
    register_feature_handler("Alpha158", _alpha158_factory)


def _alpha158_factory(config: FeatureDatasetConfig) -> Any:
    from qlib.contrib.data.handler import Alpha158  # type: ignore[import-not-found]

    return Alpha158(
        instruments=config.instruments,
        start_time=config.train_start,
        end_time=config.test_end,
        fit_start_time=config.train_start,
        fit_end_time=config.train_end,
    )


# Module-load: seed the registry with the built-in handler. Only on the
# first import — ``importlib.reload`` or an indirect re-import through
# another module must not wipe custom factories already registered by the
# application. The guard mirrors the common "init-once" pattern.
if not _FEATURE_HANDLER_REGISTRY:
    _reset_feature_handler_registry_to_defaults()


class FeatureDatasetBuilder:
    """Builds a qlib DatasetH from an Alpha158 handler.

    Usage::

        result = FeatureDatasetBuilder.build(FeatureDatasetConfig(
            instruments="csi300",
            feature_handler="Alpha158",
            train_start="2022-01-01", train_end="2024-12-31",
            valid_start="2025-01-01", valid_end="2025-06-30",
            test_start="2025-07-01",  test_end="2025-12-31",
        ))
        dataset = result.dataset  # pass to ModelTrainer
    """

    @classmethod
    def build(
        cls,
        config: FeatureDatasetConfig,
        *,
        pit_provider: Any | None = None,
        cache_dir: Path | str | None = None,
    ) -> FeatureDatasetResult:
        """Build a qlib DatasetH for the given config.

        Parameters
        ----------
        config
            Frozen dataset config (instruments, handler, date splits).
        pit_provider
            Optional :class:`src.pit.query.PITDataProvider`. When
            supplied, the builder asserts that the canonical qlib
            config's ``provider_uri`` matches the PIT provider's
            ``_provider_uri`` and raises if they disagree. The dataset
            itself is still built via qlib's ``DatasetH`` (qlib is the
            feature handler contract — we don't reach inside it), so
            this acts as a **PIT-correctness guard rather than a
            functional swap**: it catches the operator footgun of
            calling ``init_qlib_canonical(provider_uri=legacy_dir)``
            while passing a PIT-corrected provider to the trainer,
            which would silently train on legacy survivorship-biased
            bins. Phase D.2 opt-in; default ``None`` preserves legacy
            behaviour.
        cache_dir
            Optional directory for the feature-dataset pickle cache.
            When set, the builder hashes the config (plus the qlib
            provider's bundle tag, when available) and looks for a
            cached ``FeatureDatasetResult`` before rebuilding. On
            cache miss, the freshly-built result is pickled to the
            directory. Cache failures (corrupt blob, disk full,
            permission) are logged at WARNING and treated as cache
            misses — never as build failures. The cache is bypassed
            when ``pit_provider`` is supplied (live PIT state cannot
            be safely serialised). Default ``None`` preserves the
            legacy zero-cache code path. See
            ``openspec/changes/add-feature-dataset-cache/`` for the
            full contract.
        """
        cls._validate(config)

        if pit_provider is not None:
            cls._validate_pit_provider_alignment(pit_provider)
            # pit_provider has live state we cannot serialise safely;
            # never read from or write to the cache on this path.
            cache_active = False
            cache_key: str | None = None
        else:
            cache_active = cache_dir is not None
            cache_key = None
            if cache_active:
                from src.data._feature_dataset_cache import (  # noqa: PLC0415
                    cache_get,
                    compute_cache_key,
                    read_bundle_tag,
                )
                # bundle_tag derivation is best-effort — read_bundle_tag
                # returns "unknown" when no manifest exists, which is
                # still a stable input to the hash so the cache is
                # consistent across calls within the same session.
                try:
                    from src.core.qlib_runtime import (  # noqa: PLC0415
                        get_canonical_qlib_config,
                    )
                    canonical = get_canonical_qlib_config()
                    bundle_uri = (
                        canonical.provider_uri if canonical else None
                    )
                except Exception:  # noqa: BLE001
                    bundle_uri = None
                bundle_tag = read_bundle_tag(bundle_uri)
                cache_key = compute_cache_key(config, bundle_tag=bundle_tag)
                cached = cache_get(cache_dir, cache_key)
                if cached is not None:
                    _log.info(
                        "feature-dataset cache hit (key=%s, instruments=%s, "
                        "test=%s~%s) — skipping handler + 3× prepare().",
                        cache_key, config.instruments,
                        config.test_start, config.test_end,
                    )
                    return cached

        try:
            from qlib.data.dataset import DatasetH  # type: ignore[import-not-found]
        except ImportError as exc:
            raise FeatureDatasetBuilderError(
                "qlib is not importable; cannot build feature dataset."
            ) from exc

        handler = cls._build_handler(config)

        dataset = DatasetH(
            handler=handler,
            segments={
                "train": [config.train_start, config.train_end],
                "valid": [config.valid_start, config.valid_end],
                "test": [config.test_start, config.test_end],
            },
        )

        train_df = dataset.prepare("train", col_set="feature")
        valid_df = dataset.prepare("valid", col_set="feature")
        test_df = dataset.prepare("test", col_set="feature")

        if train_df.empty:
            raise FeatureDatasetBuilderError(
                "Train segment is empty. Check instruments and date ranges."
            )
        if valid_df.empty:
            raise FeatureDatasetBuilderError(
                "Valid segment is empty. Check instruments and date ranges "
                f"(valid_start={config.valid_start}, valid_end={config.valid_end})."
            )
        if test_df.empty:
            raise FeatureDatasetBuilderError(
                "Test segment is empty. Check instruments and date ranges "
                f"(test_start={config.test_start}, test_end={config.test_end})."
            )

        result = FeatureDatasetResult(
            dataset=dataset,
            train_shape=(train_df.shape[0], train_df.shape[1]),
            valid_shape=(valid_df.shape[0], valid_df.shape[1]),
            test_shape=(test_df.shape[0], test_df.shape[1]),
            feature_columns=tuple(str(c) for c in train_df.columns),
        )

        if cache_active and cache_key is not None:
            from src.data._feature_dataset_cache import cache_put  # noqa: PLC0415
            cache_put(cache_dir, cache_key, result)

        return result

    @classmethod
    def _validate_pit_provider_alignment(cls, pit_provider: Any) -> None:
        """When the caller passes a PIT provider, the qlib canonical
        runtime MUST already be initialised AND its ``provider_uri``
        MUST match the PIT provider's. Otherwise the operator
        accidentally trains on the legacy survivorship-biased bins
        while *thinking* they're using the PIT-corrected provider.
        Phase D.2 guard.
        """
        from src.core.qlib_runtime import (
            get_canonical_qlib_config,
            is_canonical_qlib_initialized,
        )

        if not is_canonical_qlib_initialized():
            raise FeatureDatasetBuilderError(
                "pit_provider was supplied but canonical qlib runtime is "
                "not initialised. Call init_qlib_canonical(...) with the "
                "PIT-corrected provider_uri before building a PIT dataset."
            )
        canonical = get_canonical_qlib_config()
        if canonical is None:
            # Should not happen given is_canonical_qlib_initialized()
            # above, but defensive.
            raise FeatureDatasetBuilderError(
                "Canonical qlib config is unavailable despite "
                "is_canonical_qlib_initialized() == True. Internal "
                "inconsistency — investigate qlib_runtime state."
            )
        # Normalise both paths the same way init_qlib_canonical does
        # (case-insensitive on Windows, realpath, etc.). The simplest
        # safe comparison: resolve absolute path with the same
        # _normalize_provider_uri pipeline qlib_runtime uses.
        from src.core.qlib_runtime import _normalize_provider_uri
        live_norm = canonical.provider_uri  # already normalised at init
        pit_uri_raw = str(getattr(pit_provider, "_provider_uri", ""))
        if not pit_uri_raw:
            raise FeatureDatasetBuilderError(
                "pit_provider has no readable _provider_uri attribute "
                f"(got {pit_provider!r}). Expected a PITDataProvider."
            )
        pit_norm = _normalize_provider_uri(pit_uri_raw)
        if live_norm != pit_norm:
            raise FeatureDatasetBuilderError(
                "PIT provider / qlib provider_uri mismatch — training "
                "would silently use the wrong provider. "
                f"qlib canonical provider_uri = {live_norm!r}; "
                f"pit_provider._provider_uri = {pit_norm!r}. "
                "Re-init qlib with the PIT-corrected provider before "
                "passing pit_provider to build()."
            )

    @classmethod
    def _validate(cls, config: FeatureDatasetConfig) -> None:
        if not is_canonical_qlib_initialized():
            raise FeatureDatasetBuilderError(
                "Canonical qlib runtime is not initialized. "
                "Call src.core.qlib_runtime.init_qlib_canonical(...) first."
            )

        if not str(config.instruments or "").strip():
            raise FeatureDatasetBuilderError("instruments must be a non-empty string.")

        if config.feature_handler not in _FEATURE_HANDLER_REGISTRY:
            raise FeatureDatasetBuilderError(
                f"feature_handler must be one of {list_supported_feature_handlers()}, "
                f"got '{config.feature_handler}'."
            )

        date_fields = (
            ("train_start", config.train_start),
            ("train_end", config.train_end),
            ("valid_start", config.valid_start),
            ("valid_end", config.valid_end),
            ("test_start", config.test_start),
            ("test_end", config.test_end),
        )
        parsed = {}
        for name, value in date_fields:
            if not str(value or "").strip():
                raise FeatureDatasetBuilderError(f"{name} must be a non-empty ISO date string.")
            parsed[name] = parse_iso_date(value, error_cls=FeatureDatasetBuilderError)

        if parsed["train_start"] > parsed["train_end"]:
            raise FeatureDatasetBuilderError("train_start must be <= train_end.")
        if parsed["valid_start"] > parsed["valid_end"]:
            raise FeatureDatasetBuilderError("valid_start must be <= valid_end.")
        if parsed["test_start"] > parsed["test_end"]:
            raise FeatureDatasetBuilderError("test_start must be <= test_end.")

        # Enforce chronological ordering: train < valid < test
        if parsed["train_end"] >= parsed["valid_start"]:
            raise FeatureDatasetBuilderError(
                f"train_end ({config.train_end}) must be before valid_start ({config.valid_start}). "
                "Overlapping train/valid ranges cause data leakage."
            )
        if parsed["valid_end"] >= parsed["test_start"]:
            raise FeatureDatasetBuilderError(
                f"valid_end ({config.valid_end}) must be before test_start ({config.test_start}). "
                "Overlapping valid/test ranges cause data leakage."
            )

        # Alpha158 label-lookahead embargo. Previously this check lived
        # only in web/operator_ui/training_guards.py, so CLI / main.py /
        # direct API callers bypassed it entirely — they could submit
        # adjacent-day boundaries with silent label leakage. Now both
        # entry points consume the shared validator in
        # src/data/_segment_embargo.py.
        cls._validate_embargo(config, parsed)

    @classmethod
    def _validate_embargo(
        cls,
        config: FeatureDatasetConfig,
        parsed: dict,
    ) -> None:
        """Run the shared Alpha158 label-lookahead embargo check.

        Loads the trading calendar from the already-initialised qlib
        runtime (``_validate`` has already asserted
        ``is_canonical_qlib_initialized``). When qlib's calendar is
        unreachable for any reason — degraded providers, calendar
        provider not yet bound, calendar slice empty — we log INFO
        and skip rather than block, because the embargo check is a
        leakage guard, not a substitute for the qlib initialisation
        chain. The skip is loud enough to grep in operator logs.

        The check is mandatory for ``feature_handler == "Alpha158"``.
        Other handlers (e.g. ``MinedFactor``) may have different label
        lookahead semantics and currently opt out — adding their own
        embargo policy is the handler author's responsibility.
        """
        if config.feature_handler != "Alpha158":
            return

        from src.data._segment_embargo import (  # noqa: PLC0415
            LABEL_LOOKAHEAD_DAYS,
            validate_segment_embargo,
        )

        calendar = cls._load_trading_calendar(
            start=config.train_end, end=config.test_start,
        )
        if not calendar:
            _log.info(
                "Skipping Alpha158 label embargo check: qlib's trading "
                "calendar is unreachable or empty over %s ~ %s. The "
                "operator UI's training_guards still enforces the same "
                "check before launch; CLI / main.py callers in this "
                "environment lose the leakage guard for this run.",
                config.train_end, config.test_start,
            )
            return

        errors = validate_segment_embargo(
            train_end=parsed["train_end"],
            valid_start=parsed["valid_start"],
            valid_end=parsed["valid_end"],
            test_start=parsed["test_start"],
            calendar=calendar,
            lookahead_days=LABEL_LOOKAHEAD_DAYS,
        )
        if errors:
            joined = "\n  - ".join(errors)
            raise FeatureDatasetBuilderError(
                "Alpha158 label embargo violation — refusing to build "
                "feature dataset (silent label leakage would inflate "
                "OOS metrics):\n  - " + joined,
            )

    @staticmethod
    def _load_trading_calendar(*, start: str, end: str):
        """Return a list of ``date`` objects from qlib's calendar.

        Returns ``None`` on any failure (qlib import error, calendar
        provider not bound, empty slice) — callers treat ``None`` as
        "skip the embargo check with INFO". Never raises.
        """
        try:
            import pandas as pd  # noqa: PLC0415
            from qlib.data import D  # type: ignore[import-not-found]
            cal = D.calendar(start_time=start, end_time=end)
            out = [pd.Timestamp(d).date() for d in cal]
            return out if out else None
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _build_handler(config: FeatureDatasetConfig) -> Any:
        try:
            factory = _FEATURE_HANDLER_REGISTRY[config.feature_handler]
        except KeyError as exc:
            raise FeatureDatasetBuilderError(
                f"feature_handler must be one of {list_supported_feature_handlers()}, "
                f"got '{config.feature_handler}'."
            ) from exc
        return factory(config)
