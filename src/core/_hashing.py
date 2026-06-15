"""Stable content hashing shared across the pipeline layer.

The same idiom — JSON-canonicalise a payload (sorted keys, ``default=str``) then
SHA-256 it — backs the pipeline run-dir suffix, the run-catalog config
fingerprint, and the result-artifact stable hash. Centralised here so the
canonicalisation, which determines hash identity across re-runs, is defined
once.

NOT to be confused with ``walk_forward/_resume.compute_config_fingerprint``,
which is a semantics-aware, exclude-driven config identity — deliberately a
separate concept and left untouched.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def sha256_canonical(payload: dict[str, Any], *, length: int | None = None) -> str:
    """SHA-256 of ``payload`` JSON-canonicalised (``sort_keys=True, default=str``).

    ``length`` truncates the hex digest to a prefix (callers use a short prefix
    for a directory suffix); ``None`` returns the full 64-char digest.
    """
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return digest[:length] if length is not None else digest
