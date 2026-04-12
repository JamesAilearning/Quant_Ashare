"""Centralized logging configuration for V2 pipeline.

Usage in any module::

    from src.core.logger import get_logger

    logger = get_logger(__name__)
    logger.info("Starting backtest...")
"""

from __future__ import annotations

import logging
import sys


_CONFIGURED = False


def setup_logging(level: int = logging.INFO) -> None:
    """Configure root logger for the V2 pipeline.

    Safe to call multiple times — only the first call takes effect.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

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


def get_logger(name: str) -> logging.Logger:
    """Return a logger under the ``src`` namespace.

    Automatically calls :func:`setup_logging` on first use.
    """
    setup_logging()
    return logging.getLogger(name)
