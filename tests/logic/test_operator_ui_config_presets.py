"""Regression tests for operator UI preset helpers."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from web.operator_ui.config_presets import (
    CUSTOM_PRESET_NAME,
    BUILT_IN_PRESET_NAMES,
    list_preset_names,
    load_preset,
    sanitise_preset_name,
)


class ConfigPresetTests(unittest.TestCase):
    def test_list_preset_names_includes_saved_presets_before_custom(self) -> None:
        with TemporaryDirectory() as tmp:
            presets_dir = Path(tmp)
            (presets_dir / "default.yaml").write_text("mode: pipeline\n", encoding="utf-8")
            (presets_dir / "my_preset.yaml").write_text("mode: pipeline\n", encoding="utf-8")

            self.assertEqual(
                list_preset_names(presets_dir),
                (*BUILT_IN_PRESET_NAMES, "my_preset", CUSTOM_PRESET_NAME),
            )

    def test_load_preset_sanitises_name_before_path_lookup(self) -> None:
        with TemporaryDirectory() as tmp:
            presets_dir = Path(tmp)
            (presets_dir / "escape.yaml").write_text("mode: pipeline\n", encoding="utf-8")

            self.assertEqual(load_preset(presets_dir, "../escape"), {"mode": "pipeline"})

    def test_sanitise_preset_name_rejects_empty_names(self) -> None:
        self.assertEqual(sanitise_preset_name("../../"), "")


if __name__ == "__main__":
    unittest.main()
