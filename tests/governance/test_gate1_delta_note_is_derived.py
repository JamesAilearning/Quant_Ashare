"""§7's Gate-1 deviation finding must follow the numbers, not a fixed script.

The narrative sits directly beneath the §5 delta table, so a sentence that
always says "大体坐实, 由 2018-2020 过渡期滞后驱动" contradicts its own table
the moment the inputs move (codex #425 r11). Both the verdict and the cause are
therefore derived — and on the current membership the whole Gate-1 comparison
is suppressed, so without these tests the derivation would ship unexercised.
"""

from __future__ import annotations

import unittest

from scripts.research.gate3_step_a_coverage_report import gate1_delta_note

YEARS = (2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025)


def _flat(value: float) -> dict[int, float]:
    return {y: value for y in YEARS}


def _early_only(value: float) -> dict[int, float]:
    """All of the deviation lands in the 2018-2020 transition window."""
    return {y: (value if y <= 2020 else 0.0) for y in YEARS}


def _late_only(value: float) -> dict[int, float]:
    return {y: (0.0 if y <= 2020 else value) for y in YEARS}


class Gate1DeltaNoteIsDerived(unittest.TestCase):
    def test_small_deltas_read_as_confirmed(self) -> None:
        note = gate1_delta_note({f"f{i}": _flat(0.4) for i in range(6)})
        self.assertIn("大体坐实", note)
        self.assertIn("6 个可比字段中 6 个", note)

    def test_large_deltas_do_not_read_as_confirmed(self) -> None:
        """The verdict flips when most fields fall outside ±1pp."""
        fields = {f"f{i}": _flat(5.0) for i in range(5)}
        fields["ok"] = _flat(0.2)
        note = gate1_delta_note(fields)
        self.assertNotIn("大体坐实", note)
        self.assertIn("偏离已不算小", note)

    def test_transition_cause_is_claimed_only_when_concentrated_early(self) -> None:
        note = gate1_delta_note({
            "rd_exp": _early_only(-8.0),
            "contract_liab": _early_only(-5.0),
            "revenue": _flat(0.1),
        })
        self.assertIn("非数据缺失", note)
        self.assertNotIn("不对成因下结论", note)

    def test_no_cause_is_claimed_when_the_deviation_is_late(self) -> None:
        note = gate1_delta_note({
            "rd_exp": _late_only(-8.0),
            "contract_liab": _late_only(-5.0),
            "revenue": _flat(0.1),
        })
        self.assertIn("不对成因下结论", note)
        self.assertNotIn("非数据缺失", note)

    def test_one_late_worst_field_is_enough_to_withhold_the_cause(self) -> None:
        """The cause needs EVERY named field to be early-concentrated.

        Naming two fields and justifying only one would attribute a cause the
        data supports for half the evidence.
        """
        note = gate1_delta_note({
            "rd_exp": _early_only(-8.0),
            "contract_liab": _late_only(-5.0),
            "revenue": _flat(0.1),
        })
        self.assertIn("不对成因下结论", note)

    def test_the_named_worst_fields_are_the_measured_worst(self) -> None:
        note = gate1_delta_note({
            "small": _flat(0.1),
            "biggest": _flat(-9.0),
            "second": _flat(4.0),
        })
        self.assertIn("biggest -9.0pp", note)
        self.assertIn("second +4.0pp", note)
        self.assertNotIn("small", note)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
