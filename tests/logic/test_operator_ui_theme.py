"""Regression tests for operator UI design-system theme helpers."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


class OperatorUiThemeTests(unittest.TestCase):
    def test_preferences_round_trip(self) -> None:
        from web.operator_ui.theme import UserPreferences, load_preferences, save_preferences

        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "preferences.json"
            expected = UserPreferences(theme="dark", color_convention="western")

            save_preferences(expected, path)

            self.assertEqual(load_preferences(path), expected)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), expected.to_json_dict())

    def test_invalid_preferences_fall_back_to_defaults(self) -> None:
        from web.operator_ui.theme import UserPreferences

        self.assertEqual(
            UserPreferences.from_mapping({"theme": "sepia", "color_convention": "other"}),
            UserPreferences(),
        )

    def test_theme_css_contains_required_token_selectors(self) -> None:
        from web.operator_ui.theme import load_theme_css

        css = load_theme_css()

        self.assertIn("--bg-page", css)
        self.assertIn("--text-primary", css)
        self.assertIn("--positive", css)
        self.assertIn("--negative", css)
        self.assertIn('data-qv2-theme="dark"', css)
        self.assertIn('data-qv2-color-convention="chinese"', css)
        self.assertIn("font-variant-numeric: tabular-nums", css)
        self.assertIn("prefers-reduced-motion", css)

    def test_preference_script_sets_document_attributes(self) -> None:
        from web.operator_ui.theme import UserPreferences, preference_attribute_script

        script = preference_attribute_script(
            UserPreferences(theme="dark", color_convention="western")
        )

        self.assertIn("data-qv2-theme", script)
        self.assertIn('"dark"', script)
        self.assertIn("data-qv2-color-convention", script)
        self.assertIn('"western"', script)

    def test_app_registers_design_system_page_and_theme_injection(self) -> None:
        source = Path("web/operator_ui/app.py").read_text(encoding="utf-8")

        self.assertIn("inject_theme", source)
        self.assertIn("render_appearance_controls", source)
        self.assertIn("design_system.py", source)


if __name__ == "__main__":
    unittest.main()
