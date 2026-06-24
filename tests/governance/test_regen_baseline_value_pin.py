"""Governance (CI-runnable, no bundle): pin the REGEN-2 canonical baseline.

The deterministic reproduction lock lives in the CI-real replay
(``test_walk_forward_replay_baseline_regen2``, which replays the frozen REGEN-2
scores against the committed mini-bundle on the canonical numpy<2 stack). This
test runs in the fast suite — it only READS the committed canonical baseline JSON —
and guards that the committed REGEN-2 canonical headline + its mandated framing are
present, so a future reader can never see the number without its context:

1. the headline sits in a two-sided band that brackets the REGEN-2 canonical mean
   fold IR (~0.28) and EXCLUDES the REGEN-A price-index value (0.48), the old
   T+2/limit-permissive value (0.37), the OFF-PIN ② figure (0.16 — a regeneration on
   the wrong numpy major would land here, so the band machine-catches it), and 0;
2. the mandated framing is committed alongside the number — corrected semantics
   (T+1 + ST), the statistical caveat (SE/noise, not a strategy improvement, not
   predictive of live), the **applied** total-return (SH000300TR) benchmark basis,
   the per-fold block (>=23), and the fold-0 degenerate-tie-break known-limitation
   (so the 0.28 is never read as robust signal strength). See docs/baseline_regen2.md.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

BASELINE_FIXTURE = (
    _PROJECT_ROOT / "tests" / "regression" / "fixtures" / "walk_forward_baseline_metrics.json"
)

# The REGEN-2 canonical mean fold IR (~0.278) must be distinguishable from every
# value it superseded, WITHOUT hardcoding the exact signed-off float (that lives in
# the fixture; the CI-real replay pins it exactly):
#   - REGEN-A price-index baseline ........ 0.4815  (excluded above the band)
#   - old T+2 / limit-permissive .......... 0.3672  (excluded above the band)
#   - OFF-PIN ② figure (numpy 2.4.4) ...... 0.162   (excluded below the band)
# NOTE: ~0.278 is INFLATED above the off-pin 0.16 entirely by fold-0's degenerate-
# score sort-tie-break artifact (IR -0.889 -> +1.767 on the canonical stack), which
# also inflated the variance (SE ~0.43, CI straddles zero). It is NOT robust signal
# strength — see the fold0_known_limitation pin below and docs/baseline_regen2.md.
BAND_LOW = 0.20
BAND_HIGH = 0.35
REGEN_A_PRICE = 0.4815
OLD_T2_BASELINE = 0.3672
OFF_PIN_REGEN2 = 0.162


class RegenBaselineValuePinTests(unittest.TestCase):
    def setUp(self) -> None:
        if not BASELINE_FIXTURE.is_file():
            self.skipTest(f"baseline fixture not found at {BASELINE_FIXTURE}.")
        self.data = json.loads(BASELINE_FIXTURE.read_text(encoding="utf-8"))

    def test_headline_is_in_the_regen2_canonical_band(self) -> None:
        ir = float(self.data["aggregate_metrics"]["mean_information_ratio"])
        self.assertGreater(
            ir, BAND_LOW,
            f"committed mean_information_ratio={ir} is at/below {BAND_LOW}: it looks "
            f"like the OFF-PIN ② figure (~{OFF_PIN_REGEN2}) — a regeneration on the "
            "wrong numpy major (>=2). The canonical anchor must be generated on the "
            "numpy<2 pin; regenerate there (see fold0_known_limitation).",
        )
        self.assertLess(
            ir, BAND_HIGH,
            f"committed mean_information_ratio={ir} is at/above {BAND_HIGH}: it looks "
            f"like the REGEN-A price-index ({REGEN_A_PRICE}) or old-T2 "
            f"({OLD_T2_BASELINE}) value, not the REGEN-2 total-return canonical (~0.28). "
            "If this is an intentional re-baseline, update this band + re-sign.",
        )

    def test_corrected_semantics_provenance_present(self) -> None:
        prov = self.data.get("_provenance", {})
        sem = str(prov.get("semantics", ""))
        self.assertIn("T+1", sem, "provenance must record the T+1 execution correction (PR-C).")
        self.assertIn(
            "limit", sem.lower(),
            "provenance must record the close-derived price-limit correction (PR-D).",
        )
        self.assertIn("ST", sem, "provenance must record the PIT ST exclusion (PR-F).")
        self.assertIn(
            "REGEN-2", str(prov.get("regen", "")),
            "provenance must mark this as a REGEN-2 frozen-score replay (it is now canonical).",
        )

    def test_statistical_caveat_committed_with_the_number(self) -> None:
        caveat = str(self.data.get("_provenance", {}).get("statistical_caveat", ""))
        for token in ("SE", "noise", "NOT", "live"):
            self.assertIn(
                token, caveat,
                f"statistical_caveat must keep the '{token}' framing so the headline "
                "IR is never read as a strategy improvement / predictive of live.",
            )

    def test_total_return_benchmark_is_applied(self) -> None:
        prov = self.data.get("_provenance", {})
        self.assertEqual(
            "SH000300TR", str(prov.get("benchmark_code", "")),
            "the canonical baseline must measure excess against the SH000300TR "
            "total-return index (the TR benchmark is APPLIED, not deferred).",
        )
        note = str(prov.get("benchmark_note", ""))
        self.assertIn(
            "total-return", note.lower(),
            "benchmark_note must record the total-return basis (deferral is closed).",
        )
        self.assertNotIn(
            "deferred", note.lower(),
            "benchmark_note must NOT still say the total-return switch is 'deferred' — "
            "PR-2 applied it.",
        )

    def test_per_fold_block_present_for_23_folds(self) -> None:
        per_fold = self.data.get("per_fold", [])
        self.assertGreaterEqual(
            len(per_fold), 23,
            "REGEN-2 baseline must carry a per_fold block (>=23 real folds) so the "
            "replay test can pin every fold, not just the aggregate.",
        )

    def test_fold0_degenerate_known_limitation_recorded(self) -> None:
        # Machine-ize the PR-1 honesty caveat: the headline 0.28 is propped up by
        # fold-0's degenerate-score tie-break artifact, so that limitation must travel
        # with the committed number and can never be silently dropped.
        limitation = str(self.data.get("_provenance", {}).get("fold0_known_limitation", ""))
        self.assertTrue(limitation, "provenance must carry a fold0_known_limitation block.")
        low = limitation.lower()
        for token in ("degenerate", "tie-break"):
            self.assertIn(
                token, low,
                f"fold0_known_limitation must record the '{token}' framing (fold-0's "
                "headline contribution is a degenerate-score tie-break artifact, not signal).",
            )


if __name__ == "__main__":
    unittest.main()
