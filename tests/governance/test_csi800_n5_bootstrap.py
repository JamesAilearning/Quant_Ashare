"""Governance: CSI800 N5 bootstrap cutover (PR-C' of
2026-07-20-csi800-n5-production-promotion).

Pins the PRE-REGISTERED bootstrap arithmetic — these windows were
fixed BEFORE ignition (R1-DP-C / tasks §PR-C': 跑前钉死), so a later
edit is a governed change, not a tuning knob:

  * three staggered members, training terminals one quarter apart,
    24-month rolling train windows, ~3-month valid windows;
  * the same numbers the serving manifest will be validated against
    (PR-A' pins: fit_end gaps in [75,100], train span in [700,745]);
  * the csi800 mandatory guard trio + GPU device on every member;
  * the bootstrap path's deliberate divergence from the maintenance
    path: member gate windows are NOT recency-bound (the members are
    staggered into the past by protocol), while the ensemble gate's
    trailing quarter still is.
"""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

import yaml

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_PRESETS = _PROJECT_ROOT / "config" / "presets"

# The pre-registered windows (trading-calendar exact, bundle tail
# 2026-06-17). train | valid | test.
_BOOTSTRAP_WINDOWS = {
    "m1": (("2023-08-14", "2025-08-13"), ("2025-08-18", "2025-11-18"),
           ("2025-11-21", "2026-01-05")),
    "m2": (("2023-11-13", "2025-11-13"), ("2025-11-18", "2026-02-13"),
           ("2026-02-26", "2026-04-09")),
    "m3": (("2024-02-19", "2026-02-13"), ("2026-02-26", "2026-05-26"),
           ("2026-05-29", "2026-06-17")),
}


def _load(name: str) -> dict:
    path = _PRESETS / f"csi800_n5_bootstrap_{name}.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


class BootstrapPresetPins(unittest.TestCase):
    def test_windows_are_the_preregistered_ones(self) -> None:
        for name, (train, valid, test) in _BOOTSTRAP_WINDOWS.items():
            cfg = _load(name)
            with self.subTest(member=name):
                self.assertEqual(train[0], cfg["train_start"])
                self.assertEqual(train[1], cfg["train_end"])
                self.assertEqual(valid[0], cfg["valid_start"])
                self.assertEqual(valid[1], cfg["valid_end"])
                self.assertEqual(test[0], cfg["test_start"])
                self.assertEqual(test[1], cfg["test_end"])

    def test_member_arithmetic_satisfies_serving_pins(self) -> None:
        from src.inference.ensemble_serving import (
            MEMBER_SPACING_DAYS_MAX,
            MEMBER_SPACING_DAYS_MIN,
            TRAIN_WINDOW_DAYS_MAX,
            TRAIN_WINDOW_DAYS_MIN,
        )

        def d(s: str) -> date:
            return date.fromisoformat(s)

        ends = []
        for name, (train, _valid, _test) in _BOOTSTRAP_WINDOWS.items():
            span = (d(train[1]) - d(train[0])).days
            with self.subTest(member=name, check="train span"):
                self.assertGreaterEqual(span, TRAIN_WINDOW_DAYS_MIN)
                self.assertLessEqual(span, TRAIN_WINDOW_DAYS_MAX)
            ends.append(d(train[1]))
        for i in range(1, len(ends)):
            gap = (ends[i] - ends[i - 1]).days
            with self.subTest(pair=i, check="quarterly stagger"):
                self.assertGreaterEqual(gap, MEMBER_SPACING_DAYS_MIN)
                self.assertLessEqual(gap, MEMBER_SPACING_DAYS_MAX)
        self.assertEqual(sorted(ends), ends, "members must be oldest->newest")

    def test_valid_windows_satisfy_gate_window_pins(self) -> None:
        # The member IC gate binds its measured window to the member's
        # own training window (PR-B' r19): strictly out of sample,
        # promptly after it, quarter-to-half-year span.
        from scripts.rotation_lib import (
            GATE_WINDOW_SPAN_DAYS_MAX,
            GATE_WINDOW_SPAN_DAYS_MIN,
            MEMBER_VALID_GAP_DAYS_MAX,
        )

        def d(s: str) -> date:
            return date.fromisoformat(s)

        for name, (train, valid, _test) in _BOOTSTRAP_WINDOWS.items():
            gap = (d(valid[0]) - d(train[1])).days
            span = (d(valid[1]) - d(valid[0])).days
            with self.subTest(member=name):
                self.assertGreater(gap, 0, "valid must follow training")
                self.assertLessEqual(gap, MEMBER_VALID_GAP_DAYS_MAX)
                self.assertGreaterEqual(span, GATE_WINDOW_SPAN_DAYS_MIN)
                self.assertLessEqual(span, GATE_WINDOW_SPAN_DAYS_MAX)

    def test_guard_trio_and_device(self) -> None:
        for name in _BOOTSTRAP_WINDOWS:
            cfg = _load(name)
            with self.subTest(member=name):
                self.assertEqual("csi800", cfg["instruments"])
                self.assertEqual("SH000906TR", cfg["benchmark_code"])
                self.assertIs(True, cfg["attribution_sleeve_grouping"])
                self.assertIs(True, cfg["risk_constraints_enabled"])
                self.assertEqual("campaign_v1",
                                 cfg["risk_constraints_calibration"])
                self.assertEqual("gpu", cfg["compute_device"])

    def test_presets_differ_only_in_windows(self) -> None:
        # Same-family configuration (R1-DP-A): three members must be
        # one protocol, not three experiments.
        window_keys = {"train_start", "train_end", "valid_start",
                       "valid_end", "test_start", "test_end"}
        loaded = {n: _load(n) for n in _BOOTSTRAP_WINDOWS}
        base = loaded["m1"]
        for name, cfg in loaded.items():
            diff = {k for k in set(base) | set(cfg)
                    if base.get(k) != cfg.get(k)}
            with self.subTest(member=name):
                self.assertTrue(
                    diff <= window_keys,
                    f"{name} diverges outside the window keys: "
                    f"{sorted(diff - window_keys)}")


class BootstrapGateSemantics(unittest.TestCase):
    def test_member_scope_recency_is_opt_out_for_bootstrap(self) -> None:
        # The maintenance path binds gate recency; the bootstrap's
        # members are staggered into the past ON PURPOSE, so the
        # executor opts out for member scope ONLY.
        import inspect

        from scripts.rotation_lib import check_gate_window

        self.assertIn("enforce_recency",
                      inspect.signature(check_gate_window).parameters)
        src = (_PROJECT_ROOT / "scripts"
               / "bootstrap_ensemble_cutover.py").read_text(
            encoding="utf-8")
        self.assertIn("enforce_recency=False", src)
        # ...and the ensemble artifact must NOT opt out: the trailing
        # quarter has to describe the present.
        ensemble_call = src.split("scope=SCOPE_ENSEMBLE", 1)[1][:400]
        self.assertNotIn("enforce_recency", ensemble_call)

    def test_status_artifact_is_written_only_by_the_bootstrap(self) -> None:
        # R1-DP-D: PR-B' ships the reader; the FIRST write is here.
        rotation = (_PROJECT_ROOT / "scripts" / "rotate_ensemble_member.py"
                    ).read_text(encoding="utf-8")
        self.assertNotIn("write_text(json.dumps(status", rotation)
        cutover = (_PROJECT_ROOT / "scripts"
                   / "bootstrap_ensemble_cutover.py").read_text(
            encoding="utf-8")
        self.assertIn("build_initial_status", cutover)
        # Refuses to overwrite an existing state.
        self.assertIn("already exists", cutover)

    def test_status_file_absent_until_the_cutover_runs(self) -> None:
        from scripts.rotation_lib import RECERT_STATUS_PATH

        # PR-C' ships the executor; the artifact itself lands with the
        # cutover commit (writing it earlier starts the 15-month clock).
        path = _PROJECT_ROOT / RECERT_STATUS_PATH
        if path.exists():
            from scripts.rotation_lib import parse_recert_status

            parse_recert_status(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
