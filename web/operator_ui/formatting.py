"""Shared display helpers — no streamlit imports, no side effects."""

from __future__ import annotations

import math


def fmt_metric(val, /):
    """Format a numeric value for display, or 'unavailable' if missing/non-finite."""
    if val is None:
        return "unavailable"
    try:
        v = float(val)
        if math.isfinite(v):
            return f"{v:.4f}"
    except (TypeError, ValueError):
        pass
    return "unavailable"
