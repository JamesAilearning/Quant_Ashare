"""Unit tests for src.core.board_heuristic.

The board heuristic is the single shared implementation used by both
:mod:`src.core.performance_attribution` and :mod:`src.core.risk_constraints`
to bucket A-share instruments by listing venue. Two things matter here:

1. The bucket *labels* are stable strings prefixed with ``board_`` — both
   call sites display them in dashboards / log lines, so renaming a
   bucket is a public-facing change. These tests pin the labels.
2. The classification *rules* (which numeric prefix maps to which board)
   were previously duplicated in two places and could drift. Now there
   is one source of truth — these tests are that source's regression
   guard.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.board_heuristic import (  # noqa: E402
    ALL_BOARDS,
    BOARD_CHINEXT,
    BOARD_HEURISTIC_TAXONOMY_ID,
    BOARD_OTHER,
    BOARD_SH_MAIN,
    BOARD_SME,
    BOARD_STAR,
    BOARD_SZ_MAIN,
    classify_instrument,
    classify_instruments,
    is_board_bucket,
)


class BucketLabelTests(unittest.TestCase):
    """Pin the user-visible bucket labels.

    These strings appear in attribution reports and risk-constraint
    log lines. Any rename is a public-facing change that should ripple
    to dashboards, so we make label drift trip a test.
    """

    def test_all_labels_start_with_board_prefix(self) -> None:
        for label in ALL_BOARDS:
            self.assertTrue(
                label.startswith("board_"),
                f"{label!r} must carry the 'board_' prefix so consumers "
                "can never mistake the heuristic for an industry classification.",
            )

    def test_taxonomy_id_is_stable_string(self) -> None:
        self.assertEqual(BOARD_HEURISTIC_TAXONOMY_ID, "a_share_board_heuristic")

    def test_all_boards_set_complete(self) -> None:
        # Iteration order does not matter, but the membership does.
        self.assertEqual(
            set(ALL_BOARDS),
            {
                BOARD_SH_MAIN,
                BOARD_SZ_MAIN,
                BOARD_SME,
                BOARD_CHINEXT,
                BOARD_STAR,
                BOARD_OTHER,
            },
        )


class ClassifyInstrumentTests(unittest.TestCase):
    def test_star_market_688(self) -> None:
        self.assertEqual(classify_instrument("SH688001"), BOARD_STAR)

    def test_chinext_300_and_301(self) -> None:
        self.assertEqual(classify_instrument("SZ300001"), BOARD_CHINEXT)
        self.assertEqual(classify_instrument("SZ301001"), BOARD_CHINEXT)

    def test_sme_002(self) -> None:
        self.assertEqual(classify_instrument("SZ002001"), BOARD_SME)

    def test_sh_main_600_601_603_605(self) -> None:
        self.assertEqual(classify_instrument("SH600000"), BOARD_SH_MAIN)
        self.assertEqual(classify_instrument("SH601398"), BOARD_SH_MAIN)
        self.assertEqual(classify_instrument("SH603259"), BOARD_SH_MAIN)
        # 605 is a valid SH main-board prefix introduced in 2020 — the
        # legacy duplicated implementations omitted it. Pin it here so
        # the shared module never regresses to the old, incomplete rule.
        self.assertEqual(classify_instrument("SH605358"), BOARD_SH_MAIN)

    def test_sz_main_000_and_001(self) -> None:
        self.assertEqual(classify_instrument("SZ000001"), BOARD_SZ_MAIN)
        self.assertEqual(classify_instrument("SZ001872"), BOARD_SZ_MAIN)

    def test_unknown_prefix_buckets_to_other(self) -> None:
        # 9xx is not a normal A-share prefix — the heuristic must not
        # raise; it returns OTHER so a stray code does not abort an
        # entire universe classification.
        self.assertEqual(classify_instrument("SH900901"), BOARD_OTHER)
        # An obviously bad input also buckets to OTHER rather than
        # raising; callers who want strict validation must check upstream.
        self.assertEqual(classify_instrument("garbage"), BOARD_OTHER)

    def test_lower_or_unprefixed_codes(self) -> None:
        # The heuristic strips literal "SH"/"SZ" only — codes without
        # those prefixes are still classifiable by their numeric head.
        self.assertEqual(classify_instrument("600000"), BOARD_SH_MAIN)
        self.assertEqual(classify_instrument("000001"), BOARD_SZ_MAIN)


class ClassifyInstrumentsTests(unittest.TestCase):
    def test_returns_dict_with_one_entry_per_input(self) -> None:
        instruments = ["SH600000", "SZ300001", "SH688001"]
        result = classify_instruments(instruments)
        self.assertEqual(set(result.keys()), set(instruments))
        self.assertEqual(result["SH600000"], BOARD_SH_MAIN)
        self.assertEqual(result["SZ300001"], BOARD_CHINEXT)
        self.assertEqual(result["SH688001"], BOARD_STAR)

    def test_empty_input_returns_empty_dict(self) -> None:
        self.assertEqual(classify_instruments([]), {})


class IsBoardBucketTests(unittest.TestCase):
    def test_recognises_each_label(self) -> None:
        for label in ALL_BOARDS:
            self.assertTrue(
                is_board_bucket(label),
                f"{label!r} should be recognised as a board bucket.",
            )

    def test_rejects_external_labels(self) -> None:
        # Labels from a hypothetical industry taxonomy must NOT be
        # mistaken for board buckets — that's the entire point of the
        # ``board_`` prefix and this guard.
        self.assertFalse(is_board_bucket("Banking"))
        self.assertFalse(is_board_bucket("Real Estate"))
        # The legacy unprefixed names must also be rejected — this is a
        # regression guard against accidental reintroduction of the old
        # labels under the "board" interface.
        self.assertFalse(is_board_bucket("SH_Main"))
        self.assertFalse(is_board_bucket("ChiNext"))


if __name__ == "__main__":
    unittest.main()
