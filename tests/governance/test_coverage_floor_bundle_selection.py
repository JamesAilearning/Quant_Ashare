"""Governance: the coverage report enforces the floor bundle of ITS universe.

Floors are per-universe (coverage is a property of the universe's issuer mix).
Adding CSI800 floors without wiring the selection would leave the report
enforcing the CSI300 bundle on CSI800 runs — which REJECTS valid coverage where
CSI800's calibrated floor is lower (contract_liab 0.21 vs 0.12) and SILENTLY
ACCEPTS a regression where it is higher (oper_cost 0.97 vs 0.98). A constant
with no consumer is a dangling guard; this pins the wiring (codex #425 P1).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.research.gate3_step_a_coverage_report import (  # noqa: E402
    ReportError,
    resolve_floor_bundle,
)
from src.research.financial_pit_coverage_floors import (  # noqa: E402
    ADV_CONTRACT_COALESCE_FLOOR,
    COVERAGE_FLOORS,
    CSI800_ADV_CONTRACT_COALESCE_FLOOR,
    CSI800_COVERAGE_FLOORS,
    CSI800_FLOOR_PROVENANCE,
    FLOOR_PROVENANCE,
)


class FloorBundleSelectionTests(unittest.TestCase):
    def test_csi300_selects_the_csi300_bundle(self) -> None:
        floors, coalesce, provenance = resolve_floor_bundle(
            "csi300", Path("instruments/csi300.txt"))
        self.assertEqual(floors, dict(COVERAGE_FLOORS))
        self.assertEqual(coalesce, ADV_CONTRACT_COALESCE_FLOOR)
        self.assertEqual(provenance, FLOOR_PROVENANCE)

    def test_csi800_selects_the_csi800_bundle(self) -> None:
        floors, coalesce, provenance = resolve_floor_bundle(
            "csi800", Path("instruments/csi800.txt"))
        self.assertEqual(floors, dict(CSI800_COVERAGE_FLOORS))
        self.assertEqual(coalesce, CSI800_ADV_CONTRACT_COALESCE_FLOOR)
        self.assertEqual(provenance, CSI800_FLOOR_PROVENANCE)

    def test_selected_bundles_actually_differ(self) -> None:
        # guards the test above from passing vacuously if the two bundles ever
        # became identical objects — the whole point is that they differ.
        csi300, _c3, _p3 = resolve_floor_bundle(
            "csi300", Path("instruments/csi300.txt"))
        csi800, _c8, _p8 = resolve_floor_bundle(
            "csi800", Path("instruments/csi800.txt"))
        self.assertNotEqual(csi300, csi800)
        # the two documented directions of a mis-selection:
        self.assertLess(csi800["contract_liab"], csi300["contract_liab"])
        self.assertGreater(csi800["oper_cost"], csi300["oper_cost"])

    def test_mismatched_universe_and_instruments_file_is_refused(self) -> None:
        # passing the flag but pointing it at the other universe's members is
        # the same misconfiguration one step later.
        with self.assertRaisesRegex(ReportError, "csi800"):
            resolve_floor_bundle("csi300", Path("instruments/csi800.txt"))
        with self.assertRaisesRegex(ReportError, "csi300"):
            resolve_floor_bundle("csi800", Path("instruments/csi300.txt"))

    def test_unknown_universe_is_refused(self) -> None:
        with self.assertRaisesRegex(ReportError, "unknown floors universe"):
            resolve_floor_bundle("csi1000", Path("instruments/custom.txt"))

    def test_custom_instruments_filename_is_allowed(self) -> None:
        # a filename naming NO known universe must not block a deliberate
        # custom-universe run (the cross-check targets obvious mismatches only).
        floors, _c, _p = resolve_floor_bundle(
            "csi800", Path("instruments/my_custom_sleeve.txt"))
        self.assertEqual(floors, dict(CSI800_COVERAGE_FLOORS))

    def test_report_cli_requires_an_explicit_universe(self) -> None:
        # a silently-defaulted bundle is how the wrong floors get enforced, so
        # the flag must be REQUIRED (argparse exits 2 when it is absent).
        from scripts.research import gate3_step_a_coverage_report as rep

        with self.assertRaises(SystemExit) as ctx:
            rep.main([
                "--store-dir", "x", "--instruments-file", "y",
                "--calendar", "z",
            ])
        self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
