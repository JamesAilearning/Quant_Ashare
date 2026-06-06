"""Governance: the committed C1 baseline fixture MUST be consistent with the
canonical config's ST-mask setting.

Why this guard exists (Codex P2 on #223, the "or hide" hole)
------------------------------------------------------------
`config_walk.yaml` (which `walk_forward_baseline_config.yaml` extends) now
enables the PIT historical-ST mask via `namechange_path`. If the committed
`walk_forward_baseline_metrics.json` was generated WITHOUT it, the RUN_E2E
regression compares an ST-excluded run against an includes-ST baseline. The
drift test has a +/-5% tolerance, so when the ST impact is small it would PASS
and SILENTLY leave `main` with a stale baseline (the includes-ST C1 metrics) —
the process step ("regenerate before merge") alone can't be enforced by the
drift test.

This **non-E2E** guard (pure file comparison — no qlib, no bundle, no backtest)
upgrades that from a process promise to a CI-enforced invariant: it FAILS until
the operator regenerates + commits the baseline fixture on-branch. It runs in
the fast suite, so the inconsistency is visible at CI time, not only RUN_E2E.

The fixture is operator-managed (git-ignored by default per
tests/regression/fixtures/README.md); when absent this test skips, mirroring
test_walk_forward_aggregate_baseline.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.core._yaml_loader import load_yaml_with_inheritance  # noqa: E402

_FIXTURES = _PROJECT_ROOT / "tests" / "regression" / "fixtures"
_BASELINE_CONFIG = _FIXTURES / "walk_forward_baseline_config.yaml"
_BASELINE_METRICS = _FIXTURES / "walk_forward_baseline_metrics.json"


class BaselineStProvenanceConsistencyTests(unittest.TestCase):
    def test_baseline_records_st_mask_when_config_enables_it(self) -> None:
        if not _BASELINE_METRICS.is_file():
            self.skipTest("baseline metrics fixture absent (operator-managed)")
        cfg = load_yaml_with_inheritance(_BASELINE_CONFIG)
        namechange_path = cfg.get("namechange_path")
        st_enabled = isinstance(namechange_path, str) and bool(namechange_path.strip())
        if not st_enabled:
            self.skipTest("ST mask not enabled in the resolved baseline config")
        metrics = json.loads(_BASELINE_METRICS.read_text(encoding="utf-8"))
        config_keys = metrics.get("_provenance", {}).get("config_keys", [])
        self.assertIn(
            "namechange_path", config_keys,
            msg=(
                "STALE BASELINE: the resolved walk-forward config enables the ST "
                "mask (namechange_path is set) but the committed "
                "walk_forward_baseline_metrics.json was generated WITHOUT it "
                "(_provenance.config_keys lacks 'namechange_path'), so it still "
                "records the includes-ST C1 metrics. Regenerate it under "
                "RUN_E2E:\n  RUN_E2E=1 python "
                "scripts/generate_regression_baseline.py "
                "tests/regression/fixtures/walk_forward_baseline_config.yaml\n"
                "then eyeball the drift (expect small on csi300; check the "
                "fold_NN_st_mask_audit.csv drop set) and commit the new fixture "
                "on this branch before merge (C2-d PR2)."
            ),
        )


if __name__ == "__main__":
    unittest.main()
