"""The coverage report's candidate-window inputs MUST equal the frozen formulas.

`gate3_step_a_coverage_report.CANDIDATE_FIELDS` decides, per candidate, which
fields' as-of coverage gates the "earliest reliable year" the report publishes —
and that year is what an experiment's start date gets read off. The adjudicated
formulas live in `gate4a_ic_evaluator` (`C1/C2/C3_FIELDS`), which implements the
frozen pre-registration.

If the two drift, the report publishes a window for a factor nobody runs: a
field the formula never reads can delay a window (as `rd_exp` did before #425
r8 — the frozen erratum's OMIT formulation makes it MOOT for every candidate),
or a field the formula does read can be missing from the window computation and
let an unavailable year through. The second direction is the dangerous one, so
this pins EQUALITY, not containment.

The dependency runs one way — `gate4a_ic_evaluator` imports from
`gate3_step_a_coverage_report`, so the report cannot import the evaluator back.
This test is the join point.
"""

from __future__ import annotations

import pathlib
import re
import unittest


class CandidateWindowFieldsMatchFrozenFormulas(unittest.TestCase):
    def test_every_candidate_window_uses_exactly_the_formula_inputs(self) -> None:
        from scripts.research.gate3_step_a_coverage_report import CANDIDATE_FIELDS
        from scripts.research.gate4a_ic_evaluator import (
            C1_FIELDS,
            C2_FIELDS,
            C3_FIELDS,
        )

        frozen = {
            "C1 GPA": C1_FIELDS,
            "C2 PROF": C2_FIELDS,
            "C3 cash-OP": C3_FIELDS,
        }
        self.assertEqual(
            set(CANDIDATE_FIELDS), set(frozen),
            "the report and the frozen formulas must cover the same candidates",
        )
        for candidate, formula_fields in frozen.items():
            with self.subTest(candidate=candidate):
                self.assertEqual(
                    set(CANDIDATE_FIELDS[candidate]), set(formula_fields),
                    f"{candidate}: the window is derived from "
                    f"{sorted(CANDIDATE_FIELDS[candidate])} but the adjudicated "
                    f"formula reads {sorted(formula_fields)}",
                )

    def test_moot_fields_are_absent_from_every_window(self) -> None:
        """Non-vacuity: the two fields #425 r8 removed stay removed.

        Without this, someone could satisfy the equality test by re-adding a
        field to BOTH sides — which would silently re-open the frozen erratum's
        OMIT decision (rd_exp) or turn C3 back into a cash-flow factor
        (n_cashflow_act) rather than the pure-balance-sheet accrual.
        """
        from scripts.research.gate3_step_a_coverage_report import CANDIDATE_FIELDS

        for candidate, fields in CANDIDATE_FIELDS.items():
            with self.subTest(candidate=candidate):
                self.assertNotIn("rd_exp", fields)
                self.assertNotIn("n_cashflow_act", fields)


class PublishedReportMatchesTheGenerator(unittest.TestCase):
    """The CHECKED-IN report is what most readers consume, not this script.

    A generator fixed in isolation leaves the published artifact stating the
    old semantics forever (codex #425 r9). The report therefore PRINTS each
    candidate's input fields, and this parses them back and pins them to
    `CANDIDATE_FIELDS` — so a field-set change that is not accompanied by a
    regenerated report fails here rather than shipping a stale conclusion.
    """

    REPORT = (
        pathlib.Path(__file__).resolve().parents[2]
        / "docs" / "research" / "gate3_step_a_pit_coverage_report.md"
    )

    def _published_fields(self) -> dict[str, set[str]]:
        text = self.REPORT.read_text(encoding="utf-8")
        found: dict[str, set[str]] = {}
        for line in text.splitlines():
            m = re.match(r"^- `([^`]+)`: (`.+`)\s*$", line)
            if m is None:
                continue
            found[m.group(1)] = set(re.findall(r"`([^`]+)`", m.group(2)))
        return found

    def test_the_published_report_lists_the_current_field_sets(self) -> None:
        from scripts.research.gate3_step_a_coverage_report import CANDIDATE_FIELDS

        published = self._published_fields()
        self.assertTrue(
            published,
            f"{self.REPORT} lists no candidate input fields — regenerate it "
            "with scripts/research/gate3_step_a_coverage_report.py",
        )
        self.assertEqual(
            set(published), set(CANDIDATE_FIELDS),
            "the published report and the generator disagree on the candidate set",
        )
        for candidate, fields in CANDIDATE_FIELDS.items():
            with self.subTest(candidate=candidate):
                self.assertEqual(
                    published[candidate], set(fields),
                    f"{candidate}: the published report was generated from "
                    f"{sorted(published[candidate])}; regenerate it",
                )

    def test_the_published_report_does_not_call_rd_exp_a_window_driver(self) -> None:
        """The stale conclusions, not just the stale table (codex #425 r9)."""
        text = self.REPORT.read_text(encoding="utf-8")
        self.assertNotIn("rd_exp 季度末 as-of 细分(C2 窗口判定)", text)
        self.assertNotIn("**C2 最早可靠期 = 2019**", text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
