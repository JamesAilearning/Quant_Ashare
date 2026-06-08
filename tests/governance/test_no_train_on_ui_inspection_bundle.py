"""Governance: the operator UI must NEVER invite training on a non-production
Tushare inspection bundle (``…/operator_ui/results/<job>/qlib_provider``).

U1 (unify / retire-publisher prep) closed a footgun: three UI copy spots told
the operator to paste a ``results/.../qlib_provider`` path into a training /
backtest ``provider_uri``. That bundle is a one-off, inspection-only artifact —
training on it silently uses non-production data. This test pins the footgun
shut so it cannot regress:

1. Any UI page that mentions ``qlib_provider`` must carry an explicit
   do-not-train warning (``请勿``).
2. The specific legacy invitation phrasings must never reappear.
"""

from __future__ import annotations

import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_UI_FILES = (
    _ROOT / "web" / "operator_ui" / "pages" / "tushare.py",
    _ROOT / "web" / "operator_ui" / "pages" / "_results_render.py",
)

# Legacy "fill this path into your training provider_uri" phrasings. These were
# the footgun; they must not come back. (Use 填到 = "fill into"; the new copy
# uses 请勿…作为 = "do NOT use as", which is intentionally different.)
_BANNED_INVITATIONS = (
    "页面的 ``provider_uri`` 字段",          # old tushare.py header
    "作为「配置运行」页的 ``provider_uri``",   # old tushare.py post-submit info
    "填到训练运行的 provider_uri",            # old _results_render.py results view
)


class NoTrainOnUiInspectionBundleTests(unittest.TestCase):
    def test_ui_pages_that_mention_qlib_provider_carry_a_do_not_train_warning(self) -> None:
        for path in _UI_FILES:
            src = path.read_text(encoding="utf-8")
            if "qlib_provider" in src:
                self.assertIn(
                    "请勿", src,
                    f"{path.name} mentions qlib_provider but lacks a do-not-train "
                    "(请勿) warning — the train-on-inspection-bundle footgun may "
                    "have regressed.",
                )

    def test_legacy_training_invitation_phrases_are_gone(self) -> None:
        for path in _UI_FILES:
            src = path.read_text(encoding="utf-8")
            for banned in _BANNED_INVITATIONS:
                self.assertNotIn(
                    banned, src,
                    f"{path.name} still contains the legacy training-invitation "
                    f"phrase {banned!r}; the UI must not invite training on a "
                    "non-production results/.../qlib_provider bundle.",
                )

    def test_config_run_refuses_inspection_bundle_on_every_launch_path(self) -> None:
        """The non-production refusal must cover BOTH launch modes. ``pipeline``
        goes through ``validate_pipeline_training_inputs``; ``walk_forward`` does
        NOT — so config_run.py must call ``non_production_bundle_error`` directly
        on the walk_forward branch AND as a mode-agnostic pre-launch check.
        Regression for codex P1 on PR #231 (walk_forward could bypass the
        inspection-bundle refusal)."""
        src = (_ROOT / "web" / "operator_ui" / "pages" / "config_run.py").read_text(
            encoding="utf-8",
        )
        self.assertGreaterEqual(
            src.count("non_production_bundle_error("), 2,
            "config_run.py must apply non_production_bundle_error on the "
            "walk_forward path AND as a mode-agnostic pre-launch check — "
            "otherwise a walk_forward launch bypasses the inspection-bundle "
            "refusal (codex P1).",
        )


if __name__ == "__main__":
    unittest.main()
