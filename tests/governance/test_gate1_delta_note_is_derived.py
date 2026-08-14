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

    def test_an_exact_tie_does_not_read_as_confirmed(self) -> None:
        """50/50 is not a majority — the tie falls to the weaker claim.

        `>=` would round an even split up into a confirmation (codex #425 r12).
        """
        fields: dict[str, dict[int, float]] = {
            f"in{i}": _flat(0.3) for i in range(3)
        }
        fields.update({f"out{i}": _flat(4.0) for i in range(3)})
        note = gate1_delta_note(fields)
        self.assertIn("6 个可比字段中 3 个", note)
        self.assertNotIn("大体坐实", note)
        self.assertIn("偏离已不算小", note)

    def test_one_over_half_does_read_as_confirmed(self) -> None:
        """Non-vacuity for the tie fix: a true majority still confirms."""
        fields: dict[str, dict[int, float]] = {
            f"in{i}": _flat(0.3) for i in range(4)
        }
        fields.update({f"out{i}": _flat(4.0) for i in range(3)})
        note = gate1_delta_note(fields)
        self.assertIn("7 个可比字段中 4 个", note)
        self.assertIn("大体坐实", note)

    def test_no_cause_is_ever_claimed(self) -> None:
        """WHERE the deviation sits is measured; WHY is not (codex #425 r13).

        Early concentration is equally consistent with as-of disclosure lag, an
        incomplete historical store, and provider gaps in those years, and this
        function sees only deltas-by-year — none of the evidence that would
        separate them. Replaces the earlier
        `test_transition_cause_is_claimed_only_when_concentrated_early`, which
        pinned a causal claim the inputs cannot support.
        """
        for label, build in (("early", _early_only), ("late", _late_only)):
            with self.subTest(shape=label):
                note = gate1_delta_note({
                    "rd_exp": build(-8.0),
                    "contract_liab": build(-5.0),
                    "revenue": _flat(0.1),
                })
                self.assertIn("成因不由本表判定", note)
                self.assertNotIn("as-of 滞后)", note)
                self.assertNotIn("非数据缺失", note)

    def test_the_concentration_itself_is_still_reported(self) -> None:
        """Dropping the CAUSE must not drop the MEASUREMENT."""
        early = gate1_delta_note({
            "rd_exp": _early_only(-8.0),
            "contract_liab": _early_only(-5.0),
            "revenue": _flat(0.1),
        })
        self.assertIn("集中在 2018-2020", early)
        late = gate1_delta_note({
            "rd_exp": _late_only(-8.0),
            "contract_liab": _late_only(-5.0),
            "revenue": _flat(0.1),
        })
        self.assertIn("并未集中在 2018-2020", late)

    def test_one_late_worst_field_withholds_the_concentration_claim(self) -> None:
        """The concentration claim needs EVERY named field to be early.

        Naming two fields and evidencing one would report a concentration the
        data supports for half the evidence.
        """
        note = gate1_delta_note({
            "rd_exp": _early_only(-8.0),
            "contract_liab": _late_only(-5.0),
            "revenue": _flat(0.1),
        })
        self.assertIn("并未集中在 2018-2020", note)

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
