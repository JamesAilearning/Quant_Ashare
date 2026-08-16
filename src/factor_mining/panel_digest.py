"""Canonical digest of a fundamental panel factory's output.

The injected panel factory is the ONE seam through which code the miner
does not import (the research-side bridge) shapes a run's panel. Config
values, data digests and store fingerprints all stay identical when the
CALLABLE is swapped, so the run must record an identity that is bound to
the factory's BEHAVIOR — computed here, by trusted code, from the
factory's returned frames. A self-reported name/version pair would let
two semantically different factories claim the same identity, which is
exactly the substitution this digest exists to catch.

The digest covers ALL behavior-affecting outputs — values, availability
evidence, AND report periods (current and prior generations alike).
Dropping periods would let two factories agree on values + evidence yet
hand evaluation different terminal-level alignment masks.

Pure hashlib/numpy/pandas — no qlib, no ``src.pit`` (D5), and no
``src.research``: the factory is called by the seam owner and this
module only ever sees plain frames.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping

import numpy as np
import pandas as pd

_SEP = b"\x1f"  # unit separator: unambiguous joins for hashed text


def _hash_axis(h: hashlib._Hash, axis) -> None:
    h.update(_SEP.join(str(v).encode("utf-8") for v in axis))
    h.update(b"\x1e")


def _hash_value_frame(h: hashlib._Hash, frame: pd.DataFrame) -> None:
    arr = frame.to_numpy(dtype="float64", copy=True)
    # Canonicalize NaN bit patterns: tobytes() is bit-exact, and two NaNs
    # with different payloads would hash differently while comparing equal
    # nowhere else in the system.
    arr[np.isnan(arr)] = np.nan
    h.update(np.ascontiguousarray(arr).tobytes())


def _hash_object_frame(h: hashlib._Hash, frame: pd.DataFrame) -> None:
    cells = frame.to_numpy(dtype=object).ravel()
    h.update(_SEP.join(
        b"" if pd.isna(v) else str(v).encode("utf-8") for v in cells
    ))
    h.update(b"\x1e")


def fundamental_output_sha256(
    values: Mapping[str, pd.DataFrame],
    evidence: Mapping[str, pd.DataFrame],
    periods: Mapping[str, pd.DataFrame],
) -> str:
    """Digest of the factory's full output triple.

    Keys are visited in sorted order and each frame contributes its index,
    its columns and its cells, so the digest is invariant to mapping
    insertion order but sensitive to any single cell, any relabeling and
    any reshaping. Value frames hash their float64 bytes (bit-exact after
    NaN canonicalization); evidence and period frames hash canonical cell
    text with NA as the empty string.
    """
    h = hashlib.sha256()
    for section, mapping in (
        (b"values", values), (b"evidence", evidence), (b"periods", periods),
    ):
        h.update(section)
        h.update(b"\x1d")
        for key in sorted(mapping):
            frame = mapping[key]
            h.update(str(key).encode("utf-8"))
            h.update(b"\x1d")
            _hash_axis(h, frame.index)
            _hash_axis(h, frame.columns)
            if section == b"values":
                _hash_value_frame(h, frame)
            else:
                _hash_object_frame(h, frame)
    return h.hexdigest()


def periods_fingerprint(
    periods: Mapping[str, pd.DataFrame] | None,
) -> str | None:
    """Cache-comparability key for a periods mapping (None stays None).

    The GP engine invalidates its fitness cache when the coverage mask
    changes; scores computed under different terminal-level alignment
    masks are just as incomparable, and the mask is a pure function of
    the period frames — so the key hashes their content, not their
    object identity.
    """
    if periods is None:
        return None
    h = hashlib.sha256()
    for key in sorted(periods):
        frame = periods[key]
        h.update(str(key).encode("utf-8"))
        h.update(b"\x1d")
        _hash_axis(h, frame.index)
        _hash_axis(h, frame.columns)
        _hash_object_frame(h, frame)
    return h.hexdigest()
