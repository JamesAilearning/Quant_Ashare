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
from datetime import date
from pathlib import Path
from typing import Any

from src.contracts._shared_validators import parse_iso_date
from src.core.qlib_runtime import is_canonical_qlib_initialized

_log = logging.getLogger(__name__)


class FeatureDatasetBuilderError(RuntimeError):
    """Raised on structural misuse or qlib failures."""


FeatureHandlerFactory = Callable[["FeatureDatasetConfig"], Any]
HandlerCacheIdentity = str | Callable[[], str]
_FEATURE_HANDLER_REGISTRY: dict[str, FeatureHandlerFactory] = {}
# Maps handler-name → cache identity. Three shapes:
#   * absent / mapped to None → handler has NO declared cache identity;
#     the feature-dataset cache MUST disable itself for this handler
#     (safety default — handlers with mutable bundle state, like
#     MinedFactor before audit P2, would otherwise serve stale entries
#     across rebinds).
#   * str → constant identity (e.g. ``Alpha158`` is fully determined by
#     the registered name).
#   * Callable[[], str] → computed identity (e.g. MinedFactor's identity
#     depends on the currently-bound bundle's pool dir, PIT provider,
#     delisted registry, and pool parquet contents).
_FEATURE_HANDLER_CACHE_IDENTITY: dict[str, HandlerCacheIdentity | None] = {}


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
    # Holding horizon H in trading days (buy T+1 close, sell T+1+H close).
    # H=1 is today's behavior byte-identical: the Alpha158 label expression
    # ``Ref($close, -(H+1))/Ref($close, -1) - 1`` reproduces qlib's hard-coded
    # default character-for-character, the cache-key payload is unchanged, and
    # the embargo stays at LABEL_LOOKAHEAD_DAYS. Non-positive / non-int values
    # refuse fail-loud in ``_validate`` (defence-in-depth: also in
    # ``label_lookahead_days``).
    label_horizon_days: int = 1


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
    cache_identity: HandlerCacheIdentity | None = None,
) -> None:
    """Register a qlib feature handler factory by explicit name.

    ``cache_identity`` declares whether the feature-dataset cache can
    safely include datasets produced by this handler:

    * ``None`` (default, conservative)  Cache disabled for this
      handler — callers see a cache miss every time. Use this when
      the handler's output depends on mutable global state that the
      cache key cannot fingerprint.
    * ``str``  Constant identity. Suitable for stateless handlers
      (e.g. ``Alpha158`` whose output depends only on the qlib
      bundle + ``FeatureDatasetConfig`` fields the cache key
      already includes).
    * ``Callable[[], str]``  Computed identity. Called each time the
      cache key is composed. Suitable for handlers whose bundle
      state (e.g. MinedFactor's pool dir, PIT provider, registry)
      changes between training runs and must be reflected in the
      cache key.

    A re-registration with ``replace=True`` also replaces the
    associated identity, so binding a new MinedFactor pool produces
    a fresh cache identity.
    """

    handler_name = str(name or "").strip()
    if not handler_name:
        raise FeatureDatasetBuilderError("feature handler name must be non-empty.")
    if not callable(factory):
        raise FeatureDatasetBuilderError("feature handler factory must be callable.")
    if handler_name in _FEATURE_HANDLER_REGISTRY and not replace:
        raise FeatureDatasetBuilderError(
            f"feature handler {handler_name!r} is already registered."
        )
    if cache_identity is not None:
        if not (isinstance(cache_identity, str) or callable(cache_identity)):
            raise FeatureDatasetBuilderError(
                "cache_identity must be a str, a callable returning str, "
                f"or None — got {type(cache_identity).__name__}."
            )
        # Reject empty-string identities at registration time (Codex
        # P2 on PR #158). Returning ``""`` from ``get_…_identity``
        # falls into ``compute_cache_key``'s ``"_no_handler_identity_"``
        # sentinel, which is the SAME bucket as "handler has no
        # identity at all" — meaning a mutable handler with a
        # config-sourced empty identity would silently collide with
        # other empty-identity handlers and serve stale cache
        # entries. The safety contract says missing/invalid identity
        # disables the cache; failing loud here is more honest than
        # silently bucketing.
        if isinstance(cache_identity, str) and not cache_identity.strip():
            raise FeatureDatasetBuilderError(
                "cache_identity is an empty/whitespace-only string. "
                "Pass ``None`` to opt the handler out of caching, or "
                "supply a non-empty identity string. Empty strings "
                "would silently collide with the no-identity bucket "
                "and serve stale entries."
            )
    _FEATURE_HANDLER_REGISTRY[handler_name] = factory
    _FEATURE_HANDLER_CACHE_IDENTITY[handler_name] = cache_identity


def get_feature_handler_cache_identity(name: str) -> str | None:
    """Resolve the registered cache identity for ``name``.

    Returns ``None`` when:

    * The handler is not registered.
    * The handler was registered without a ``cache_identity``
      (the safe-default ``None`` declaration).
    * The identity descriptor is a string but empty / whitespace-only
      (defence-in-depth — ``register_feature_handler`` rejects this
      at registration time too, but a direct in-place mutation of the
      registry dict could bypass that).
    * A callable identity raised when invoked (we treat that as
      "no identity → disable cache" rather than propagating, so a
      broken identity callable never aborts the build).
    * A callable identity returns an empty string or non-string
      value (same rationale as the constant-string empty check —
      empty would silently collide with the no-identity bucket).

    Callers (the feature-dataset cache layer) treat ``None`` as
    "cache disabled for this handler".
    """
    descriptor = _FEATURE_HANDLER_CACHE_IDENTITY.get(name)
    if descriptor is None:
        return None
    if isinstance(descriptor, str):
        # Defence-in-depth: matches the registration-time check.
        if not descriptor.strip():
            return None
        return descriptor
    try:
        value = descriptor()
    except Exception:  # noqa: BLE001 — best-effort
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    return value


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
    _FEATURE_HANDLER_CACHE_IDENTITY.clear()
    # Alpha158 is stateless given the FeatureDatasetConfig fields the
    # cache key already includes (instruments + date splits + bundle
    # tag). A constant identity is therefore safe and lets the cache
    # serve Alpha158 datasets.
    register_feature_handler(
        "Alpha158", _alpha158_factory, cache_identity="alpha158_default",
    )


def alpha158_label_expression(label_horizon_days: int) -> str:
    """The Alpha158 label for holding horizon H: ``Ref($close, -(H+1))/Ref($close, -1) - 1``.

    H=1 reproduces qlib's hard-coded default CHARACTER-FOR-CHARACTER
    (``Ref($close, -2)/Ref($close, -1) - 1``) — pinned by test; the default
    path must stay byte-identical (REGEN-2 anchor)."""
    return f"Ref($close, -{label_horizon_days + 1})/Ref($close, -1) - 1"


def _alpha158_factory(config: FeatureDatasetConfig) -> Any:
    from qlib.contrib.data.handler import Alpha158

    return Alpha158(
        instruments=config.instruments,
        start_time=config.train_start,
        end_time=config.test_end,
        fit_start_time=config.train_start,
        fit_end_time=config.train_end,
        # qlib's Alpha158 accepts a label override (kwargs.pop("label", ...));
        # for H=1 this equals the handler's own default, so passing it is
        # behavior-identical (the expression string is pinned by test).
        label=(
            [alpha158_label_expression(config.label_horizon_days)],
            ["LABEL0"],
        ),
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
                # Resolve the handler's cache identity. A handler
                # registered without one (the safe default) is opted
                # OUT of caching here — serving stale bundles across
                # different MinedFactor pools, for example, would be
                # silent and misleading. See
                # ``register_feature_handler(cache_identity=...)``.
                handler_identity = get_feature_handler_cache_identity(
                    config.feature_handler,
                )
                if handler_identity is None:
                    _log.info(
                        "feature-dataset cache skipped: handler %r has "
                        "no declared cache_identity. Register the "
                        "handler with a cache_identity (str or "
                        "callable) to enable caching for it.",
                        config.feature_handler,
                    )
                    # Disable cache for this build but continue on
                    # the normal build path below.
                    cache_active = False
                else:
                    # bundle_tag derivation is best-effort — read_bundle_tag
                    # returns "unknown" when no manifest exists.
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
                    cache_key = compute_cache_key(
                        config,
                        bundle_tag=bundle_tag,
                        handler_identity=handler_identity,
                    )
                    cached = cache_get(cache_dir, cache_key)
                    if cached is not None:
                        _log.info(
                            "feature-dataset cache hit (key=%s, "
                            "instruments=%s, test=%s~%s) — skipping "
                            "handler + 3× prepare().",
                            cache_key, config.instruments,
                            config.test_start, config.test_end,
                        )
                        return cached

        try:
            from qlib.data.dataset import DatasetH
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

        h = config.label_horizon_days
        if not isinstance(h, int) or isinstance(h, bool) or h < 1:
            raise FeatureDatasetBuilderError(
                f"label_horizon_days must be a positive integer (holding days, "
                f"T+1 close -> T+1+H close); got {h!r}."
            )
        if h != 1 and config.feature_handler != "Alpha158":
            # Only the Alpha158 factory consumes the horizon (it overrides the
            # handler's label). For any other handler a non-default horizon
            # would be SILENTLY IGNORED — the run would claim H=5 while
            # training on the handler's own label. Refuse instead.
            raise FeatureDatasetBuilderError(
                f"label_horizon_days={h} is only supported for feature_handler="
                f"'Alpha158'; handler '{config.feature_handler}' defines its own "
                "label and would silently ignore the horizon. Use the default "
                "(1) or add horizon support to that handler first."
            )

        date_fields = (
            ("train_start", config.train_start),
            ("train_end", config.train_end),
            ("valid_start", config.valid_start),
            ("valid_end", config.valid_end),
            ("test_start", config.test_start),
            ("test_end", config.test_end),
        )
        parsed: dict[str, date] = {}
        for name, value in date_fields:
            if not str(value or "").strip():
                raise FeatureDatasetBuilderError(f"{name} must be a non-empty ISO date string.")
            parsed_val = parse_iso_date(value, error_cls=FeatureDatasetBuilderError)
            # ``parse_iso_date`` returns ``None`` only when its input
            # is empty / whitespace — we just rejected that case above,
            # so this narrows ``date | None`` → ``date`` for mypy
            # without changing observable behaviour.
            assert parsed_val is not None
            parsed[name] = parsed_val

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
        parsed: dict[str, Any],
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
            label_lookahead_days,
            validate_segment_embargo,
        )

        calendar = cls._load_trading_calendar(
            start=config.train_end, end=config.test_start,
        )
        if not calendar:
            # Fail-closed (Codex P1 on PR #157): we've already required
            # ``is_canonical_qlib_initialized() == True`` above; if
            # ``D.calendar`` then can't return anything over the
            # configured boundaries, the qlib init must be degraded
            # (calendar provider not bound, bundle missing the
            # ``calendars/`` dir, slice outside coverage, etc.).
            # Silently skipping the embargo check at this point
            # re-opens the exact leakage path this PR is meant to
            # close. Refuse to build instead.
            raise FeatureDatasetBuilderError(
                "Alpha158 label embargo check requires an accessible "
                "qlib trading calendar, but ``D.calendar`` returned no "
                f"dates over {config.train_end} ~ {config.test_start} "
                "even though canonical qlib init reported success. "
                "Refusing to build — silently skipping the embargo "
                "check would re-introduce the boundary-row label "
                "leakage this guard exists to prevent. Investigate the "
                "calendar provider binding (qlib bundle's "
                "``calendars/`` directory, provider_uri pointing at "
                "the right bundle, etc.)."
            )

        errors = validate_segment_embargo(
            train_end=parsed["train_end"],
            valid_start=parsed["valid_start"],
            valid_end=parsed["valid_end"],
            test_start=parsed["test_start"],
            calendar=calendar,
            # horizon-driven: H=1 -> LABEL_LOOKAHEAD_DAYS (today's 2), H>1 -> H+1
            lookahead_days=label_lookahead_days(config.label_horizon_days),
        )
        if errors:
            joined = "\n  - ".join(errors)
            raise FeatureDatasetBuilderError(
                f"Alpha158 label embargo violation (label_horizon_days="
                f"{config.label_horizon_days}, required gap="
                f"{label_lookahead_days(config.label_horizon_days)} trading "
                "days) — refusing to build feature dataset (silent label "
                "leakage would inflate OOS metrics):\n  - " + joined,
            )

    @staticmethod
    def _load_trading_calendar(*, start: str, end: str) -> list[date] | None:
        """Return a list of ``date`` objects from qlib's calendar.

        Returns ``None`` on any failure (qlib import error, calendar
        provider not bound, empty slice). Callers must treat ``None``
        as a hard error in production paths — the embargo check
        depends on the calendar being available, and silently
        skipping it would re-introduce the leakage this module exists
        to prevent. Tests that explicitly want to bypass the embargo
        check can stub this method to return a synthetic calendar
        whose dates satisfy the test's boundaries.

        Never raises — qlib's ``D.calendar`` has a few legitimate
        failure modes (calendar provider not bound, empty slice) we
        want to translate into a uniform ``None`` for the caller to
        reject explicitly.
        """
        try:
            import pandas as pd  # noqa: PLC0415
            from qlib.data import D
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
