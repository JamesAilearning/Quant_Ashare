"""预设分类：UI 可跑 vs 战役冻结件（UI drift 审计 P2）。

审计实测：下拉框把 config/presets 下全部 36 份平铺成普通选项，其中
30 份是战役预注册/认证冻结件。选中它们时页面会显示该预设名，实际
发出去的却是日频 pipeline 配置——`extends` 不解析、`rebalance_*` /
`risk_constraint_scope` / `output_dir` 无控件被静默丢弃。操作人读到的
节奏不是将要跑的节奏，而节奏正是本项目最贵的那个变量。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from web.operator_ui.config_presets import (  # noqa: E402
    BUILT_IN_PRESET_NAMES,
    CUSTOM_PRESET_NAME,
    UI_SHAPE_MARKER_KEY,
    classify_preset_names,
    list_preset_names,
)

_PRESETS = PROJECT_ROOT / "config" / "presets"
_PAGE = PROJECT_ROOT / "web" / "operator_ui" / "pages" / "config_run.py"


class ClassificationTests(unittest.TestCase):
    def test_the_two_groups_partition_the_directory(self) -> None:
        runnable, frozen = classify_preset_names(_PRESETS)
        listed = [n for n in list_preset_names(_PRESETS) if n != CUSTOM_PRESET_NAME]
        self.assertEqual(sorted(runnable + frozen), sorted(listed))
        self.assertEqual(len(set(runnable) & set(frozen)), 0)

    def test_marker_is_the_ui_only_key(self) -> None:
        # `mode` is rejected by both runtime entrypoints, so only UI-shaped
        # files carry it — that is what makes the judgment self-maintaining.
        self.assertEqual(UI_SHAPE_MARKER_KEY, "mode")

    def test_every_runnable_preset_actually_carries_the_marker(self) -> None:
        runnable, _ = classify_preset_names(_PRESETS)
        for name in runnable:
            path = _PRESETS / f"{name.lower()}.yaml"
            if not path.is_file():
                # A built-in whose file is missing stays selectable so the
                # page never loses its default — assert exactly that case.
                self.assertIn(name, BUILT_IN_PRESET_NAMES)
                continue
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            with self.subTest(preset=name):
                self.assertIn(UI_SHAPE_MARKER_KEY, raw)

    def test_no_frozen_preset_carries_the_marker(self) -> None:
        _, frozen = classify_preset_names(_PRESETS)
        for name in frozen:
            raw = yaml.safe_load(
                (_PRESETS / f"{name}.yaml").read_text(encoding="utf-8")
            ) or {}
            with self.subTest(preset=name):
                self.assertNotIn(UI_SHAPE_MARKER_KEY, raw)

    def test_known_campaign_files_are_classified_frozen(self) -> None:
        # Spot-check the ones that would hurt most if runnable: the
        # certified winner and the production bootstrap members.
        _, frozen = classify_preset_names(_PRESETS)
        for name in (
            "csi800_cadence5_conservative_isoweek",
            "csi800_n5_bootstrap_m1",
            "csi800_n5_candidate",
        ):
            with self.subTest(preset=name):
                self.assertIn(name, frozen)

    def test_builtins_are_runnable(self) -> None:
        runnable, _ = classify_preset_names(_PRESETS)
        for name in BUILT_IN_PRESET_NAMES:
            self.assertIn(name, runnable)


class PageWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.src = _PAGE.read_text(encoding="utf-8")

    def test_dropdown_offers_runnable_presets_only(self) -> None:
        self.assertIn("classify_preset_names", self.src)
        self.assertIn(
            "preset_options = (*_runnable_presets, CUSTOM_PRESET_NAME)", self.src
        )

    def test_frozen_presets_are_listed_read_only_with_reasons(self) -> None:
        self.assertIn("战役冻结件", self.src)
        self.assertIn("extends", self.src)
        self.assertIn("run_walk_forward.py", self.src)

    def test_detect_only_considers_runnable_presets(self) -> None:
        # A frozen file reported as "active" would name an option the
        # dropdown no longer offers — the selectbox would silently fall
        # back to Default while the state said otherwise.
        detect_at = self.src.index("def _detect_preset()")
        body = self.src[detect_at : detect_at + 900]
        self.assertIn("classify_preset_names", body)
        self.assertNotIn("for name in _preset_options():", body)

    def test_production_preset_no_longer_claims_to_be_production(self) -> None:
        # config/presets/production.yaml is instruments=all + SH000300TR +
        # daily single model — nothing to do with the csi800 / N5 / weekly
        # serving configuration, and it really runs when selected.
        self.assertIn("全市场基线", self.src)
        self.assertNotIn("Production = 全量生产", self.src)
        self.assertIn("csi800_n5_production.yaml", self.src)

    def test_page_states_it_emits_daily_research_configs(self) -> None:
        self.assertIn("日频研究配置", self.src)


if __name__ == "__main__":
    unittest.main()
