"""Governance: the canonical financial-PIT coverage floors (Gate-3 Step-A).

Pins the floors contract that closes codex #343's gap — there must be ONE
canonical field->floor mapping PER UNIVERSE, structurally consumable by
``FinancialPITDataView.assert_coverage_floor``, so a coverage regression can
never pass by omitting or mis-supplying floors. CI cannot re-measure the real
store (not ingested in CI), so this enforces the CONTRACT:

* every floor key is a charter field the view can actually serve;
* every floor is a sane fraction (0 < floor < 1);
* once populated, the floors cover every field a registered candidate (C1/C2/
  C3 per the signed charter) consumes — a candidate input can't silently lack
  a floor;
* provenance is recorded (which measurement, which rule);
* the coalesce floor sits above both component tripwires.

Every check runs over EVERY registered floor set (``_FLOOR_SETS``). Adding a
universe's floors without its guard would be a dangling-guard of its own kind
— a tripwire nobody checks the shape of — so a new set must be registered
here, and the sets must floor the SAME field set (a field measured in one
universe but not the other is drift).
"""
from __future__ import annotations

import unittest

from src.data.tushare.financial_statements import DATA_FIELDS
from src.research.financial_pit_coverage_floors import (
    ADV_CONTRACT_COALESCE_FLOOR,
    COVERAGE_FLOORS,
    CSI800_ADV_CONTRACT_COALESCE_FLOOR,
    CSI800_COVERAGE_FLOORS,
    CSI800_FLOOR_PROVENANCE,
    FLOOR_PROVENANCE,
)

_ALL_CHARTER_FIELDS = {f for fields in DATA_FIELDS.values() for f in fields}

# universe -> (floors, coalesce floor, provenance, provenance must mention)
_FLOOR_SETS: dict[str, tuple[dict[str, float], float, str, str]] = {
    "CSI300": (
        COVERAGE_FLOORS, ADV_CONTRACT_COALESCE_FLOOR, FLOOR_PROVENANCE,
        "gate3_step_a_pit_coverage_report",
    ),
    "CSI800": (
        CSI800_COVERAGE_FLOORS, CSI800_ADV_CONTRACT_COALESCE_FLOOR,
        CSI800_FLOOR_PROVENANCE, "CSI800-ever store",
    ),
}

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
        for universe, (floors, _c, _p, _m) in _FLOOR_SETS.items():
            with self.subTest(universe=universe):
                unknown = sorted(set(floors) - _ALL_CHARTER_FIELDS)
                self.assertEqual(
                    unknown, [],
                    msg=(f"{universe} floors have non-charter field(s) "
                         f"{unknown} — floors must map fields the view can "
                         "actually serve."),
                )

    def test_floor_values_are_sane_fractions(self) -> None:
        for universe, (floors, _c, _p, _m) in _FLOOR_SETS.items():
            with self.subTest(universe=universe):
                bad = {f: v for f, v in floors.items() if not (0.0 < v < 1.0)}
                self.assertEqual(
                    bad, {},
                    msg=(f"{universe} floor value(s) out of (0,1): {bad} — a "
                         "0/negative floor never fires and a >=1 floor always "
                         "fires."),
                )

    def test_candidate_inputs_all_floored_once_populated(self) -> None:
        # while floors are unpopulated (pre-measurement) this is vacuous; the
        # moment ANY floor lands, every candidate input must have one so no
        # C1/C2/C3 input can silently regress without a floor.
        for universe, (floors, _c, _p, _m) in _FLOOR_SETS.items():
            with self.subTest(universe=universe):
                if not floors:
                    self.skipTest(f"{universe} floors not yet populated")
                missing = sorted(_CANDIDATE_INPUT_FIELDS - set(floors))
                self.assertEqual(
                    missing, [],
                    msg=(f"{universe}: candidate input field(s) missing a "
                         f"canonical floor: {missing} — every C1/C2/C3 input "
                         "needs one (charter §2 / spec coverage acceptance)."),
                )

    def test_candidate_fields_are_charter_fields(self) -> None:
        # guard the test's own field list against drift from the ingest schema.
        unknown = sorted(_CANDIDATE_INPUT_FIELDS - _ALL_CHARTER_FIELDS)
        self.assertEqual(
            unknown, [],
            msg=f"_CANDIDATE_INPUT_FIELDS not in DATA_FIELDS: {unknown}",
        )

    def test_provenance_recorded(self) -> None:
        for universe, (_f, _c, provenance, must_mention) in _FLOOR_SETS.items():
            with self.subTest(universe=universe):
                self.assertIn(must_mention, provenance)
                self.assertIn("- 0.02", provenance)

    def test_coalesce_floor_guards_the_c3_consumable_union(self) -> None:
        # the C3-consumable quantity is adv_receipts∪contract_liab; its floor
        # must exist as a sane fraction AND sit ABOVE both component tripwires
        # (a collapsed union with healthy components must fail, codex #347).
        for universe, (floors, coalesce, _p, _m) in _FLOOR_SETS.items():
            with self.subTest(universe=universe):
                self.assertTrue(0.0 < coalesce < 1.0)
                if floors:
                    self.assertGreater(coalesce, floors["adv_receipts"])
                    self.assertGreater(coalesce, floors["contract_liab"])

    def test_universes_floor_the_same_field_set(self) -> None:
        # A field floored in one universe but not the other is drift: the
        # unfloored side could regress to zero coverage unnoticed while the
        # floored side stays green.
        populated = {u: set(f) for u, (f, _c, _p, _m) in _FLOOR_SETS.items() if f}
        if len(populated) < 2:
            self.skipTest("fewer than two populated floor sets")
        reference_universe, reference = next(iter(populated.items()))
        for universe, fields in populated.items():
            with self.subTest(universe=universe):
                self.assertEqual(
                    fields, reference,
                    msg=(f"{universe} floors a different field set than "
                         f"{reference_universe}: only-in-{universe}="
                         f"{sorted(fields - reference)}, "
                         f"missing-from-{universe}={sorted(reference - fields)}"),
                )


if __name__ == "__main__":
    unittest.main()
