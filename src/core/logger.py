"""Centralized logging configuration for V2 pipeline.

Usage in any module::

    from src.core.logger import get_logger

    logger = get_logger(__name__)
    logger.info("Starting backtest...")
"""

from __future__ import annotations

import logging
import sys
import threading


# Guarded by ``_CONFIG_LOCK`` so two threads racing the first
# ``get_logger`` call do not both pass the ``if _CONFIGURED`` check
# and double-attach the handler (which would emit every line twice).
# The check-then-set pattern below is *not* atomic without the lock —
# the GIL serialises individual bytecodes but not the read+write
# straddling the ``return``.
_CONFIGURED = False
_CONFIG_LOCK = threading.Lock()


def setup_logging(level: int = logging.INFO) -> None:
    """Configure root logger for the V2 pipeline.

    Safe to call multiple times — only the first call takes effect.
    Thread-safe: concurrent first callers will see exactly one handler
    attached.
    """
    global _CONFIGURED
    # Fast path: already configured, no lock needed. ``_CONFIGURED``
    # only ever transitions False → True, so a stale-True read is
    # safe; a stale-False read just enters the slow path below.
    if _CONFIGURED:
        return
    with _CONFIG_LOCK:
        if _CONFIGURED:
            return

        formatter = logging.Formatter(
            fmt="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
            datefmt="%H:%M:%S",
        )

        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(formatter)

        root = logging.getLogger("src")
        root.setLevel(level)
        root.addHandler(handler)
        # Prevent propagation to the root logger (avoids duplicate output)
        root.propagate = False
        _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a logger under the ``src`` namespace.

    Automatically calls :func:`setup_logging` on first use.
    """
    setup_logging()
    return logging.getLogger(name)
