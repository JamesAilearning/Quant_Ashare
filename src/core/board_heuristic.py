"""A-share *board* heuristic — the only shared source of board buckets.

Why this module exists
----------------------
Two call sites previously carried identical 30-line implementations of
the same function:

* ``src.experimental.risk_constraints._code_based_sector_map``
* ``src.core.performance_attribution._code_based_sector_map``

Both classified A-share instruments into six buckets by code prefix
and both called the result ``sector``. But those buckets are **boards**,
not industries — the Shanghai Main Board alone contains banks, real
estate, utilities, steel, pharmaceuticals, and so on. Calling it
``sector`` invited a dangerous misreading of every risk constraint
and Brinson attribution that flowed from it: "industry concentration
limit" or "sector allocation effect" computed off this map are really
*board* statistics, and treating them as industry statistics leaks
into downstream decisions.

The dedup here serves three goals:

1. **One implementation**. Fixing a prefix rule (new board code, etc.)
   is a one-line change in one place.
2. **Honest names**. Bucket ids are prefixed with ``board_`` so they
   can never be confused with an industry-source classification.
   Downstream dashboards and log lines carry the prefix through,
   making the coarseness visible at every reading layer.
3. **Explicit taxonomy label**. :data:`BOARD_HEURISTIC_TAXONOMY_ID`
   is a string constant callers can stamp onto their result objects
   (e.g. ``AttributionResult.sector_taxonomy``), so consumers that
   receive the output can filter / flag it.

When a real industry classification (Shenwan L1/L2, CITIC) becomes
available, build a separate module that returns the same dict shape
with a different taxonomy id; callers can pick the source at the
config layer. Do **not** repurpose this module for that — its entire
reason to exist is "the only thing this tells you is which board a
code is listed on".
"""

from __future__ import annotations

import re

from src.core.logger import get_logger

_logger = get_logger(__name__)


# Stable identifier for the taxonomy produced here. Stamp this onto
# result objects that expose a "sector" classification so consumers
# can tell the code-prefix heuristic apart from a real industry map.
BOARD_HEURISTIC_TAXONOMY_ID: str = "a_share_board_heuristic"


# Bucket labels — deliberately prefixed with ``board_`` so they can
# never be misread as industry names. Exposed so callers can iterate
# the full set (e.g. to allocate colors in a chart) without repeating
# the list.
BOARD_SH_MAIN: str = "board_SH_Main"
BOARD_SZ_MAIN: str = "board_SZ_Main"
BOARD_SME: str = "board_SME"
BOARD_CHINEXT: str = "board_ChiNext"
BOARD_STAR: str = "board_STAR"
BOARD_BSE: str = "board_BSE"  # Beijing Stock Exchange (北交所)
BOARD_OTHER: str = "board_Other"

ALL_BOARDS: tuple[str, ...] = (
    BOARD_SH_MAIN,
    BOARD_SZ_MAIN,
    BOARD_SME,
    BOARD_CHINEXT,
    BOARD_STAR,
    BOARD_BSE,
    BOARD_OTHER,
)


# Strict format: ``SH``/``SZ``/``BJ`` prefix followed by exactly six
# digits. ``str.replace`` was the previous matching strategy and would
# happily accept ``"SHSHE000001"`` (yielding ``"E000001"``) or even
# ``"SH"`` alone — silently bucketing into ``board_Other``. The regex
# locks the shape down so unfamiliar inputs go through the explicit
# WARNING + ``board_Other`` path instead of pretending to classify.
_INSTRUMENT_RE = re.compile(r"^(SH|SZ|BJ)(\d{6})$")


def classify_instrument(instrument: str) -> str:
    """Return the board bucket for a single A-share instrument code.

    Accepts ``"SH600000"`` / ``"SZ300001"`` / ``"BJ430047"`` — strict
    ``^(SH|SZ|BJ)\\d{6}$`` shape. Numeric-prefix rules:

    * ``SH``: ``688xxx`` → ``board_STAR``;
      ``600/601/603/605xxx`` → ``board_SH_Main``;
      anything else → ``board_Other``.
    * ``SZ``: ``300/301xxx`` → ``board_ChiNext``;
      ``002xxx`` → ``board_SME``;
      ``000/001xxx`` → ``board_SZ_Main``;
      anything else → ``board_Other``.
    * ``BJ``: ``4xxxxx``/``8xxxxx`` → ``board_BSE`` (Beijing Stock
      Exchange — created in 2021 to host former NEEQ Select-tier
      stocks; the previous version of this module had no awareness
      of BSE codes and bucketed them all as ``board_Other``).

    Malformed inputs (wrong prefix, wrong length, embedded duplicates,
    etc.) log a WARNING and bucket into ``board_Other`` rather than
    raising. The function is called on entire universes of instruments
    and a single bad code should not abort the whole classification —
    but the WARNING ensures the breakage shows up in logs instead of
    silently degrading the taxonomy.
    """
    match = _INSTRUMENT_RE.match(instrument)
    if match is None:
        _logger.warning(
            "board_heuristic: instrument %r does not match the strict "
            "^(SH|SZ|BJ)\\d{6}$ shape; bucketing as %s. Pass an "
            "exchange-prefixed 8-character code or filter the universe "
            "upstream.",
            instrument, BOARD_OTHER,
        )
        return BOARD_OTHER

    exchange, code = match.group(1), match.group(2)
    if exchange == "BJ":
        # BSE codes start with 4 (transitioned NEEQ Select) or 8 (newly
        # listed). Treat the whole BJ namespace as one bucket; finer
        # subdivision belongs in a real industry taxonomy.
        if code.startswith(("4", "8")):
            return BOARD_BSE
        _logger.warning(
            "board_heuristic: BJ-prefixed code %r has unexpected leading "
            "digit (expected 4 or 8); bucketing as %s.",
            instrument, BOARD_OTHER,
        )
        return BOARD_OTHER

    if exchange == "SH":
        if code.startswith("688"):
            return BOARD_STAR
        if code.startswith(("600", "601", "603", "605")):
            return BOARD_SH_MAIN
        _logger.warning(
            "board_heuristic: SH-prefixed code %r has an unrecognised "
            "numeric prefix; bucketing as %s.",
            instrument, BOARD_OTHER,
        )
        return BOARD_OTHER

    # exchange == "SZ"
    if code.startswith(("300", "301")):
        return BOARD_CHINEXT
    if code.startswith("002"):
        return BOARD_SME
    if code.startswith(("000", "001")):
        return BOARD_SZ_MAIN
    _logger.warning(
        "board_heuristic: SZ-prefixed code %r has an unrecognised "
        "numeric prefix; bucketing as %s.",
        instrument, BOARD_OTHER,
    )
    return BOARD_OTHER


def classify_instruments(instruments: list[str]) -> dict[str, str]:
    """Return ``{instrument: board_bucket}`` for a list of codes.

    Thin convenience wrapper around :func:`classify_instrument`.
    """
    return {inst: classify_instrument(inst) for inst in instruments}


def is_board_bucket(label: str) -> bool:
    """Return True iff ``label`` is one of the board bucket ids this
    module produces. Downstream consumers can use this to tell the
    heuristic-derived buckets apart from a real industry classification
    that may be layered on later.
    """
    return label in ALL_BOARDS
