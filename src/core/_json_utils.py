"""Internal JSON serialisation + canonical-hash helpers shared across the pipeline.

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

``sha256_canonical`` lives here too: it is the JSON-canonicalise-then-hash
idiom (the pipeline's run-dir suffix, run-catalog fingerprint, and
result-artifact stable hash all share it). It is a pipeline runtime helper,
not data-layer I/O — so it belongs in ``src/core/`` alongside the other shared
pipeline JSON helpers, not under ``src/data/`` (cf. AGENTS.md layer boundary).
NOT to be confused with ``walk_forward/_resume.compute_config_fingerprint``,
which is a semantics-aware, exclude-driven config identity — deliberately
separate and left untouched.
"""

from __future__ import annotations

import hashlib
import json
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
    if isinstance(obj, (int, float)) and not math.isfinite(float(obj)):
        return None
    return obj


def sha256_canonical(payload: dict[str, Any], *, length: int | None = None) -> str:
    """SHA-256 of ``payload`` JSON-canonicalised (``sort_keys=True, default=str``).

    ``length`` truncates the hex digest to a prefix (callers use a short prefix
    for a directory suffix); ``None`` returns the full 64-char digest. Defined
    once so the canonicalisation, which determines hash identity across re-runs,
    is shared by the pipeline run-dir suffix, the run-catalog fingerprint, and
    the result-artifact stable hash.
    """
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return digest[:length] if length is not None else digest
