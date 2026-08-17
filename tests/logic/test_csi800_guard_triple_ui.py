"""UI 侧 csi800 守卫三件套的行为测试（UI drift 审计 P0）。

源码钉能证明"页面发出了这三个键"，证明不了"发出的配置真的能构造"。
本文件走后端真校验器：先复现审计发现的开箱即坏（csi800 无守卫 →
构造即 raise），再证明页面守卫会在**同一条件下**响亮拒绝、且补齐后
配置可构造。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:  # qlib-bound import — the guard delegates to the canonical validator
    from src.core.pipeline import PipelineConfig
    from web.operator_ui.training_guards import validate_csi800_guard_triple

    _HAS_BACKEND = True
except Exception:  # pragma: no cover - environment-dependent
    _HAS_BACKEND = False

_PROVIDER = "D:/qlib_data/my_cn_data_pit"


@unittest.skipUnless(_HAS_BACKEND, "backend config schemas unavailable")
class Csi800GuardTripleTests(unittest.TestCase):
    def test_the_bug_reproduces_without_the_triple(self) -> None:
        # The exact shape the page used to emit for the Default preset.
        with self.assertRaises(Exception) as ctx:
            PipelineConfig(instruments="csi800", provider_uri=_PROVIDER)
        self.assertIn("attribution_sleeve_grouping", str(ctx.exception))

    def test_the_ui_guard_refuses_exactly_that_shape(self) -> None:
        errors: list[str] = []
        validate_csi800_guard_triple("csi800", False, False, "default", errors)
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("csi800", errors[0])
        # The message names every missing precondition, so the operator can
        # fix all three in one pass rather than one error per round-trip.
        for key in (
            "attribution_sleeve_grouping",
            "risk_constraints_enabled",
            "risk_constraints_calibration",
        ):
            self.assertIn(key, errors[0])

    def test_the_completed_triple_passes_guard_and_constructs(self) -> None:
        errors: list[str] = []
        validate_csi800_guard_triple("csi800", True, True, "campaign_v1", errors)
        self.assertEqual(errors, [])
        # Same values through the real backend — the guard's verdict and the
        # constructor's verdict must agree, or the page is lying either way.
        config = PipelineConfig(
            instruments="csi800",
            provider_uri=_PROVIDER,
            attribution_sleeve_grouping=True,
            risk_constraints_enabled=True,
            risk_constraints_calibration="campaign_v1",
        )
        self.assertEqual(config.instruments, "csi800")

    def test_partial_triples_are_all_refused(self) -> None:
        # Every proper subset must fail — a guard that only checks one field
        # would pass two of these.
        partials = (
            (True, False, "default"),
            (False, True, "default"),
            (False, False, "campaign_v1"),
            (True, True, "default"),
            (True, False, "campaign_v1"),
            (False, True, "campaign_v1"),
        )
        for sleeve, rc, calibration in partials:
            with self.subTest(triple=(sleeve, rc, calibration)):
                errors: list[str] = []
                validate_csi800_guard_triple(
                    "csi800", sleeve, rc, calibration, errors
                )
                self.assertEqual(len(errors), 1)

    def test_non_csi800_universes_are_untouched(self) -> None:
        # The triple is a csi800 contract — csi300 must NOT be forced into
        # campaign semantics (that would silently change what it measures).
        for universe in ("csi300", "all", "csi500"):
            with self.subTest(universe=universe):
                errors: list[str] = []
                validate_csi800_guard_triple(
                    universe, False, False, "default", errors
                )
                self.assertEqual(errors, [])

    def test_whitespace_padded_csi800_still_guarded(self) -> None:
        # instruments is a free-text field; the backend compares exactly, so
        # a padded value would slip past a naive check here and fail later.
        errors: list[str] = []
        validate_csi800_guard_triple("  csi800  ", False, False, "default", errors)
        self.assertEqual(len(errors), 1, "padded csi800 must still be guarded")


if __name__ == "__main__":
    unittest.main()
