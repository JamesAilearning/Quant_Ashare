"""A-share ST/*ST risk-warning status, derived from a stock's display name.

The single shared ST predicate for both the daily recommendation (inference,
PR1) and — once it lands — the walk-forward backtest (PR2). Inference feeds it
the *current* name from the active-stocks snapshot; the backtest will feed it
each *historical* name from the tushare ``namechange`` table to reconstruct
point-in-time ST status. Defining it once, here, keeps the two paths from
drifting on what "counts as ST".

Marker coverage — driven by the markers actually present in
``all_namechanges.parquet`` (8042 rows) and ``active_stocks.parquet``:

* **matched** (ST family): ``ST``, ``*ST``, ``SST``, ``S*ST`` and their
  resumption-day ``N``-prefixed forms (e.g. ``NST`` = an ST stock on the first
  day it resumes trading — 35 such rows in the data, all genuine ST).
* **not matched**: bare ``S`` (share-reform pending — a structural marker, not a
  risk warning), bare ``N`` / ``C`` (newly-listed / first-days marker),
  ``XD`` / ``XR`` / ``DR`` (ex-dividend / ex-rights), and Latin company names
  that merely start with the letters (``TCL``, ``GQY``, a hypothetical
  ``STAR…``).
* ``PT`` (particular transfer) is intentionally **not** matched: a pre-2007
  status absent from this bundle's data, denoting a stock already suspended
  from listing (delisting-adjacent), handled by the delisting layer.

The marker is always a LEADING token, so this is a prefix match, never a
substring test. The trailing ``(?![A-Za-z])`` guard makes ``STAR科技`` (Latin
"STAR") correctly NON-ST even though it starts with the letters ``ST``.

Known data caveat (handled by PR2, NOT here): tushare occasionally stores a
*truncated* historical name such as ``*金亚`` — the ``ST`` dropped — whose
``change_reason`` still says ``*ST``. ``is_st_name`` is a pure name predicate
and returns ``False`` for such a name; PR2's historical reconstruction must
cross-check ``change_reason`` to catch these.
"""

from __future__ import annotations

import re

# Anatomy of the leading marker token:
#   N?    optional resumption / new-listing marker  (NST -> still ST)
#   S?    optional share-reform marker              (SST, S*ST)
#   \*?   optional delisting-risk star              (*ST, S*ST)
#   ST    the special-treatment marker itself
#   (?![A-Za-z])  not followed by another letter -> excludes "STAR…" etc.
# Regex backtracking lets ``S?`` yield the 'S' to the literal ``ST`` when
# needed, so "NST" matches (N, then ST) while bare "S佳通" does not.
# The pattern is case-SENSITIVE on purpose: real ST markers are uppercase, and
# matching a lowercase "st" would false-positive on Latin names ("star…").
_ST_FAMILY = re.compile(r"^N?S?\*?ST(?![A-Za-z])")

# Full-width -> half-width for the three marker glyphs only (＊ U+FF0A,
# Ｓ U+FF33, Ｔ U+FF34). A-share data is normally half-width, but a full-width
# star is a classic tushare trap that the half-width ``\*`` would silently miss
# (a false negative = an ST stock leaking into the list). Neither the current
# active_stocks nor all_namechanges contains any (verified), so this is
# defensive: it future-proofs the SHARED predicate against a re-fetch that
# introduces them. Company-name glyphs (e.g. full-width "Ａ" in 万科Ａ) are left
# untouched — only ＊/Ｓ/Ｔ map, so they cannot create a false positive.
_FULLWIDTH_MARKER = {0xFF0A: "*", 0xFF33: "S", 0xFF34: "T"}


def is_st_name(name: str | None) -> bool:
    """True iff ``name`` carries an A-share ST-family risk-warning marker.

    Pure (no IO) -> unit-testable. ``None`` / empty -> ``False``. Full-width
    marker glyphs (＊ＳＴ) are normalised to half-width before matching.
    """
    if not name:
        return False
    return _ST_FAMILY.match(name.strip().translate(_FULLWIDTH_MARKER)) is not None


def current_st_codes(names_by_code: dict[str, str]) -> set[str]:
    """The subset of ``names_by_code`` keys whose name is ST-flagged.

    Code-format agnostic: pass ``{instrument: name}`` in whatever code
    convention the caller uses (qlib ``SZ000001`` or tushare ``000001.SZ``);
    the returned set uses the same keys.
    """
    return {code for code, nm in names_by_code.items() if is_st_name(nm)}


__all__ = ["current_st_codes", "is_st_name"]
