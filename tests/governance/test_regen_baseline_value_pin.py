"""Governance (CI-runnable, no bundle): pin the REGEN-A corrected baseline.

The deterministic reproduction lock (``test_walk_forward_replay_baseline``)
needs the real qlib bundle, so it cannot run in CI. This test runs in the fast
suite — it only READS the committed baseline JSON — and guards two things:

1. the committed headline is the CORRECTED value (clearly above the old
   T+2/limit-permissive 0.3672), not silently reverted; and
2. the mandated framing is committed alongside the number — corrected semantics
   (T+1 + ST), the statistical caveat (SE/noise, not a strategy improvement),
   the deferred total-return benchmark, and the per-fold block — so a future
   reader can never see the higher IR without the "metric correction, within
   noise" context (docs/baseline_20260616.md).
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

# The corrected baseline must be clearly distinguishable from the old
# T+2/limit-permissive 0.3672 (so a revert is caught), without hardcoding the
# exact signed-off float (that lives in the fixture; the replay test pins it
# exactly).
OLD_T2_BASELINE = 0.3672
CORRECTED_FLOOR = 0.40


class RegenBaselineValuePinTests(unittest.TestCase):
    def setUp(self) -> None:
        if not BASELINE_FIXTURE.is_file():
            self.skipTest(f"baseline fixture not found at {BASELINE_FIXTURE}.")
        self.data = json.loads(BASELINE_FIXTURE.read_text(encoding="utf-8"))

    def test_headline_is_the_corrected_value_not_the_old_t2(self) -> None:
        ir = self.data["aggregate_metrics"]["mean_information_ratio"]
        self.assertGreater(
            float(ir), CORRECTED_FLOOR,
            f"committed mean_information_ratio={ir} looks like the OLD T+2 "
            f"baseline (~{OLD_T2_BASELINE}), not the REGEN-A corrected value. "
            "If this is an intentional re-baseline, update this pin + re-sign.",
        )

    def test_corrected_semantics_provenance_present(self) -> None:
        prov = self.data.get("_provenance", {})
        sem = str(prov.get("semantics", ""))
        self.assertIn("T+1", sem, "provenance must record the T+1 execution correction.")
        self.assertIn("ST", sem, "provenance must record the PIT ST exclusion.")
        self.assertIn(
            "REGEN-A", str(prov.get("regen", "")),
            "provenance must mark this as a REGEN-A frozen-score replay.",
        )

    def test_statistical_caveat_committed_with_the_number(self) -> None:
        caveat = str(self.data.get("_provenance", {}).get("statistical_caveat", ""))
        for token in ("SE", "noise", "NOT", "live"):
            self.assertIn(
                token, caveat,
                f"statistical_caveat must keep the '{token}' framing so the "
                "higher IR is never read as a strategy improvement.",
            )

    def test_total_return_benchmark_deferral_recorded(self) -> None:
        note = str(self.data.get("_provenance", {}).get("benchmark_note", ""))
        self.assertIn(
            "REGEN-2", note,
            "benchmark_note must record that the total-return (SH000300TR) switch "
            "is deferred to REGEN-2 (excess will revise down ~2-2.5pp).",
        )

    def test_per_fold_block_present_for_22_folds(self) -> None:
        per_fold = self.data.get("per_fold", [])
        self.assertGreaterEqual(
            len(per_fold), 22,
            "baseline must carry a per_fold block (>=22 folds) so the replay "
            "test can pin every fold, not just the aggregate.",
        )


if __name__ == "__main__":
    unittest.main()
