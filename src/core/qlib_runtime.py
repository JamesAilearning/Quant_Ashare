"""Single canonical qlib runtime initialization entry point for V2.

V1 lesson: hidden coupling in app runtime initialization meant qlib was
initialized from multiple places with inconsistent configs. V2 enforces a
single auditable entry point with a re-initialization guard.

This module is the ONLY place in the canonical runtime layer that is
allowed to call ``qlib.init``. A governance regression test enforces
this boundary (see tests/governance/test_qlib_init_singleton.py).

Usage::

    from src.core.qlib_runtime import QlibRuntimeConfig, init_qlib_canonical

    init_qlib_canonical(
        QlibRuntimeConfig(
            provider_uri=r"D:/qlib_data/my_cn_data",
            region="cn",
        )
    )

Re-initialization rules:

- Calling ``init_qlib_canonical`` again with the exact same config is
  idempotent and will not re-run ``qlib.init``.
- Calling it with a different config raises :class:`QlibRuntimeInitError`
  and does not touch the already-initialized qlib state.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Optional

CANONICAL_QLIB_INIT_OWNER = "src.core.qlib_runtime"

# Module-level lock guards all reads and writes to the singleton state.
_INIT_LOCK = threading.Lock()


class QlibRuntimeInitError(RuntimeError):
    """Raised when canonical qlib initialization is inconsistent or misused."""


@dataclass(frozen=True)
class QlibRuntimeConfig:
    """Frozen configuration accepted by the canonical qlib init entry point."""

    provider_uri: str
    region: str
    expression_cache: Optional[str] = None
    dataset_cache: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.provider_uri.strip():
            raise QlibRuntimeInitError("provider_uri is required for canonical qlib init.")
        normalized_region = self.region.strip().lower()
        if normalized_region not in ("cn", "us"):
            raise QlibRuntimeInitError(
                f"Unsupported region '{normalized_region}'. Canonical runtime accepts 'cn' or 'us'."
            )
        # Normalize provider_uri and region so that dataclass __eq__
        # is not fooled by D:\\data vs D:/data or "CN" vs "cn".
        import os
        object.__setattr__(self, "provider_uri", os.path.normpath(self.provider_uri.strip()))
        object.__setattr__(self, "region", normalized_region)


_CANONICAL_CONFIG: Optional[QlibRuntimeConfig] = None
_CANONICAL_QLIB_INITIALIZED: bool = False


def init_qlib_canonical(config: QlibRuntimeConfig) -> None:
    """Initialize qlib for the canonical runtime layer.

    Thread-safe. Idempotent with respect to the exact same config.
    Raises :class:`QlibRuntimeInitError` on re-init with a different config.
    """
    global _CANONICAL_CONFIG, _CANONICAL_QLIB_INITIALIZED

    if not isinstance(config, QlibRuntimeConfig):
        raise QlibRuntimeInitError(
            "init_qlib_canonical requires a QlibRuntimeConfig instance."
        )

    with _INIT_LOCK:
        if _CANONICAL_CONFIG is not None:
            if _CANONICAL_CONFIG != config:
                raise QlibRuntimeInitError(
                    "Canonical qlib runtime already initialized with a different config. "
                    f"existing={_CANONICAL_CONFIG}, requested={config}"
                )
            # Idempotent no-op — same config, already initialized.
            return

        # Lazy import so that modules importing this file do not hard-require
        # qlib at collection time.
        try:
            import qlib  # type: ignore[import-not-found]
            from qlib.constant import REG_CN, REG_US  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - environment-dependent
            raise QlibRuntimeInitError(
                "qlib is not importable from the current Python environment. "
                "Install the pinned local qlib (see docs/qlib-pin.md) before "
                "initializing canonical runtime."
            ) from exc

        region_constant = REG_CN if config.region.strip().lower() == "cn" else REG_US

        # Guard: qlib may already be initialized at the process level (e.g.
        # test-runner resets our singleton but qlib's internal state persists,
        # or some other library grabbed qlib first with a different config).
        # Calling qlib.init() again raises RecorderInitializationError, but
        # simply skipping it would leave our canonical state lying about what
        # provider/region qlib actually resolved against. Compare against
        # qlib's live config and refuse to adopt a foreign session.
        from qlib.config import C as _qlib_C  # type: ignore[import-not-found]

        if getattr(_qlib_C, "registered", False):
            mismatch = _qlib_session_mismatch(_qlib_C, config, region_constant)
            if mismatch is not None:
                raise QlibRuntimeInitError(
                    "qlib is already initialized in this process with a different "
                    f"configuration: {mismatch}. Canonical runtime refuses to "
                    "adopt a foreign qlib session. Restart the process or align "
                    "the upstream qlib.init() call with the canonical config."
                )
        else:
            qlib.init(
                provider_uri=config.provider_uri,
                region=region_constant,
                expression_cache=config.expression_cache,
                dataset_cache=config.dataset_cache,
            )

        _CANONICAL_CONFIG = config
        _CANONICAL_QLIB_INITIALIZED = True


def get_canonical_qlib_config() -> Optional[QlibRuntimeConfig]:
    """Return the config that initialized qlib, or None if not initialized."""
    with _INIT_LOCK:
        return _CANONICAL_CONFIG


def is_canonical_qlib_initialized() -> bool:
    """Return True if canonical qlib runtime init has completed."""
    with _INIT_LOCK:
        return _CANONICAL_QLIB_INITIALIZED


def _qlib_session_mismatch(qlib_C: object, config: QlibRuntimeConfig, region_constant: object) -> Optional[str]:
    """Return a human-readable mismatch description, or None if aligned.

    Checks provider_uri (after path normalization) and region against the
    live qlib.config.C state. Returns None only when both match the incoming
    canonical config.
    """
    import os as _os

    # qlib stores provider_uri as either a single string or a dict keyed by
    # freq; normalize to a string we can compare.
    live_provider_raw = getattr(qlib_C, "provider_uri", None)
    if isinstance(live_provider_raw, dict):
        # Pick the "day" entry first, then any value
        live_provider = live_provider_raw.get("day") or next(
            iter(live_provider_raw.values()), None
        )
    else:
        live_provider = live_provider_raw

    if live_provider is None:
        return "qlib.config.C.provider_uri is unset"

    try:
        live_provider_norm = _os.path.normpath(str(live_provider).strip())
    except (TypeError, ValueError):
        live_provider_norm = str(live_provider)

    if live_provider_norm != config.provider_uri:
        return (
            f"provider_uri mismatch (live={live_provider_norm!r}, "
            f"requested={config.provider_uri!r})"
        )

    live_region = getattr(qlib_C, "region", None)
    if live_region is not None and live_region != region_constant:
        return f"region mismatch (live={live_region!r}, requested={region_constant!r})"

    return None


def _reset_canonical_qlib_runtime_for_tests() -> None:
    """Reset internal state. TEST-ONLY.

    This helper must only be called from within ``tests/``. A governance
    regression test asserts no non-test caller imports this symbol.
    """
    global _CANONICAL_CONFIG, _CANONICAL_QLIB_INITIALIZED
    with _INIT_LOCK:
        _CANONICAL_CONFIG = None
        _CANONICAL_QLIB_INITIALIZED = False
