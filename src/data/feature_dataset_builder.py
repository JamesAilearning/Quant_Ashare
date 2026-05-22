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

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from src.contracts._shared_validators import parse_iso_date
from src.core.qlib_runtime import is_canonical_qlib_initialized


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
        """
        cls._validate(config)

        if pit_provider is not None:
            cls._validate_pit_provider_alignment(pit_provider)

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

        return FeatureDatasetResult(
            dataset=dataset,
            train_shape=(train_df.shape[0], train_df.shape[1]),
            valid_shape=(valid_df.shape[0], valid_df.shape[1]),
            test_shape=(test_df.shape[0], test_df.shape[1]),
            feature_columns=tuple(str(c) for c in train_df.columns),
        )

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
