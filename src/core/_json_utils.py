"""Internal JSON serialisation helpers shared across the pipeline.

Why this module exists
----------------------
``Pipeline._write_report`` and ``WalkForwardEngine.run`` both persist
nested dicts that may carry NaN / Inf floats — SignalAnalyzer and
FactorAnalyzer encode "undefined IC / IR" as NaN so the report stays
honest about the gap, but standard JSON does not allow either token.
Python's default ``json.dump`` will happily emit the literal ``NaN``,
which downstream consumers (browsers, ``jq``, strict parsers) reject.

A single shared sanitizer here means both writers go through the same
NaN→null conversion. Previously ``_sanitize_for_json`` was a private
helper inside ``pipeline``; the walk-forward engine duplicating it would
be the kind of drift the rest of this codebase is hardening against.
"""

from __future__ import annotations

import math
from typing import Any


def _sanitize_for_json(obj: Any) -> Any:
    """Recursively convert NaN/Inf floats to ``None`` so the result
    encodes as standard JSON.

    Dispatches on ``dict`` / ``list`` / ``tuple`` and replaces any
    non-finite ``float`` it finds at the leaves. Strings, ints, bools,
    and ``None`` pass through unchanged.
    """
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_json(v) for v in obj]
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj
