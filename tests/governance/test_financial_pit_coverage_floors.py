"""Governance: the canonical financial-PIT coverage floors (Gate-3 Step-A).

Pins the floors contract that closes codex #343's gap — there must be ONE
canonical field->floor mapping, structurally consumable by
``FinancialPITDataView.assert_coverage_floor``, so a coverage regression can
never pass by omitting or mis-supplying floors. CI cannot re-measure the real
store (not ingested in CI), so this enforces the CONTRACT:

* every floor key is a charter field the view can actually serve;
* every floor is a sane fraction (0 < floor < 1);
* once populated, the floors cover every field a registered candidate (C1/C2/
  C3 per the signed charter) consumes — a candidate input can't silently lack
  a floor;
* provenance is recorded (which report, which rule).
"""
from __future__ import annotations

import unittest

from src.data.tushare.financial_statements import DATA_FIELDS
from src.research.financial_pit_coverage_floors import (
    ADV_CONTRACT_COALESCE_FLOOR,
    COVERAGE_FLOORS,
    FLOOR_PROVENANCE,
)

_ALL_CHARTER_FIELDS = {f for fields in DATA_FIELDS.values() for f in fields}

# The charter candidates' input fields (Gate-0 charter §2; int_exp is the
# charter's named alternative to fin_exp and is NOT floor-required because the
# Gate-1 memo fixed the interest term to fin_exp).
_CANDIDATE_INPUT_FIELDS = {
    # C1 GPA
    "revenue", "oper_cost", "total_assets",
    # C2 PROF
    "sell_exp", "admin_exp", "rd_exp", "fin_exp", "total_hldr_eqy_inc_min_int",
    # C3 cash-based OP
    "accounts_receiv", "inventories", "prepayment", "accounts_pay",
    "adv_receipts", "contract_liab", "n_cashflow_act",
}


class CoverageFloorContractTests(unittest.TestCase):
    def test_floor_keys_are_charter_fields(self) -> None:
        unknown = sorted(set(COVERAGE_FLOORS) - _ALL_CHARTER_FIELDS)
        self.assertEqual(
            unknown, [],
            msg=(f"COVERAGE_FLOORS has non-charter field(s) {unknown} — floors "
                 "must map fields the view can actually serve."),
        )

    def test_floor_values_are_sane_fractions(self) -> None:
        bad = {f: v for f, v in COVERAGE_FLOORS.items() if not (0.0 < v < 1.0)}
        self.assertEqual(
            bad, {},
            msg=(f"COVERAGE_FLOORS value(s) out of (0,1): {bad} — a 0/negative "
                 "floor never fires and a >=1 floor always fires."),
        )

    def test_candidate_inputs_all_floored_once_populated(self) -> None:
        # while floors are unpopulated (pre-measurement) this is vacuous; the
        # moment ANY floor lands, every candidate input must have one so no
        # C1/C2/C3 input can silently regress without a floor.
        if not COVERAGE_FLOORS:
            self.skipTest("COVERAGE_FLOORS not yet populated (pre Step-A report)")
        missing = sorted(_CANDIDATE_INPUT_FIELDS - set(COVERAGE_FLOORS))
        self.assertEqual(
            missing, [],
            msg=(f"candidate input field(s) missing a canonical floor: {missing} "
                 "— every C1/C2/C3 input needs one (charter §2 / spec coverage "
                 "acceptance)."),
        )

    def test_candidate_fields_are_charter_fields(self) -> None:
        # guard the test's own field list against drift from the ingest schema.
        unknown = sorted(_CANDIDATE_INPUT_FIELDS - _ALL_CHARTER_FIELDS)
        self.assertEqual(
            unknown, [],
            msg=f"_CANDIDATE_INPUT_FIELDS not in DATA_FIELDS: {unknown}",
        )

    def test_provenance_recorded(self) -> None:
        self.assertIn("gate3_step_a_pit_coverage_report", FLOOR_PROVENANCE)
        self.assertIn("- 0.02", FLOOR_PROVENANCE)

    def test_coalesce_floor_guards_the_c3_consumable_union(self) -> None:
        # the C3-consumable quantity is adv_receipts∪contract_liab; its floor
        # must exist as a sane fraction AND sit ABOVE both component tripwires
        # (a collapsed union with healthy components must fail, codex #347).
        self.assertTrue(0.0 < ADV_CONTRACT_COALESCE_FLOOR < 1.0)
        if COVERAGE_FLOORS:
            self.assertGreater(
                ADV_CONTRACT_COALESCE_FLOOR, COVERAGE_FLOORS["adv_receipts"])
            self.assertGreater(
                ADV_CONTRACT_COALESCE_FLOOR, COVERAGE_FLOORS["contract_liab"])


if __name__ == "__main__":
    unittest.main()
