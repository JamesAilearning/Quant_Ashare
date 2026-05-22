"""Regression tests for operator UI design-system theme helpers."""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path


class OperatorUiThemeTests(unittest.TestCase):
    def test_preferences_round_trip(self) -> None:
        from web.operator_ui.theme import UserPreferences, load_preferences, save_preferences

        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "preferences.json"
            expected = UserPreferences(
                theme="dark", color_convention="western", sidebar_collapsed=True
            )

            save_preferences(expected, path)

            self.assertEqual(load_preferences(path), expected)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), expected.to_json_dict())

    def test_invalid_preferences_fall_back_to_defaults(self) -> None:
        from web.operator_ui.theme import UserPreferences

        self.assertEqual(
            UserPreferences.from_mapping(
                {"theme": "sepia", "color_convention": "other", "sidebar_collapsed": "yes"}
            ),
            UserPreferences(),
        )

    def test_preferences_legacy_payload_without_sidebar_field(self) -> None:
        """Pre-shell preference files SHALL load without raising, defaulting
        ``sidebar_collapsed`` to False (additive schema migration)."""

        from web.operator_ui.theme import UserPreferences

        loaded = UserPreferences.from_mapping(
            {"theme": "dark", "color_convention": "western"}
        )
        self.assertEqual(
            loaded,
            UserPreferences(
                theme="dark", color_convention="western", sidebar_collapsed=False
            ),
        )

    def test_theme_css_contains_required_token_selectors(self) -> None:
        from web.operator_ui.theme import load_theme_css

        css = load_theme_css()

        self.assertIn("--bg-page", css)
        self.assertIn("--text-primary", css)
        self.assertIn("--positive", css)
        self.assertIn("--negative", css)
        self.assertIn('data-theme="dark"', css)
        self.assertIn('data-color-convention="chinese"', css)
        self.assertIn('data-qv2-theme="dark"', css)
        self.assertIn('data-qv2-color-convention="chinese"', css)
        self.assertIn("font-variant-numeric: tabular-nums", css)
        self.assertIn("prefers-reduced-motion", css)

    def test_theme_css_contains_app_shell_classes(self) -> None:
        from web.operator_ui.theme import load_theme_css

        css = load_theme_css()

        self.assertIn(".qv2-skip-link", css)
        self.assertIn(".qv2-topbar", css)
        self.assertIn(".qv2-topbar-title", css)
        self.assertIn(".qv2-topbar-actions", css)
        self.assertIn(".qv2-settings-section", css)
        self.assertIn(".qv2-mobile-only", css)

    def test_preference_script_sets_document_attributes(self) -> None:
        from web.operator_ui.theme import UserPreferences, preference_attribute_script

        script = preference_attribute_script(
            UserPreferences(theme="dark", color_convention="western")
        )

        self.assertIn("data-qv2-theme", script)
        self.assertIn("data-theme", script)
        self.assertIn('"dark"', script)
        self.assertIn("data-qv2-color-convention", script)
        self.assertIn("data-color-convention", script)
        self.assertIn('"western"', script)
        self.assertIn("localStorage", script)
        self.assertIn("qv2.theme", script)
        self.assertIn("qv2.colorConvention", script)
        self.assertIn("qv2.serverTheme", script)
        self.assertIn("qv2.serverColorConvention", script)
        self.assertIn("serverPreferenceChanged", script)
        self.assertIn("qv2SetAppearancePreference", script)

    def test_python_ui_sources_do_not_hardcode_hex_colors(self) -> None:
        pattern = re.compile(r"#[0-9A-Fa-f]{3,8}")

        offenders: list[str] = []
        for path in Path("web/operator_ui").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if pattern.search(text):
                offenders.append(str(path))

        self.assertEqual(offenders, [], "Python UI sources should use CSS tokens")

    def test_app_registers_design_system_page_and_theme_injection(self) -> None:
        source = Path("web/operator_ui/app.py").read_text(encoding="utf-8")

        self.assertIn("inject_theme", source)
        self.assertIn("design_system.py", source)

    def test_app_uses_shell_helpers_not_legacy_sidebar_expander(self) -> None:
        """App entry SHALL drive appearance from the topbar settings dialog
        rather than the legacy sidebar expander."""

        source = Path("web/operator_ui/app.py").read_text(encoding="utf-8")

        self.assertIn("render_skip_link", source)
        self.assertIn("render_topbar", source)
        self.assertIn("render_settings_dialog", source)
        # Legacy expander entry MUST NOT be in the main shell flow.
        self.assertNotIn("render_appearance_controls", source)

    def test_app_passes_sidebar_default_to_page_config(self) -> None:
        """Saved ``sidebar_collapsed`` SHALL drive ``initial_sidebar_state``
        so the user's stored preference applies on first load."""

        source = Path("web/operator_ui/app.py").read_text(encoding="utf-8")

        self.assertIn("initial_sidebar_state", source)
        self.assertIn("sidebar_collapsed", source)

    def test_skip_link_html_targets_main_anchor(self) -> None:
        from web.operator_ui.theme import SKIP_LINK_HTML

        self.assertIn('href="#qv2-main-content"', SKIP_LINK_HTML)
        self.assertIn('id="qv2-main-content"', SKIP_LINK_HTML)
        self.assertIn("qv2-skip-link", SKIP_LINK_HTML)

    def test_render_settings_dialog_is_importable(self) -> None:
        """The dialog helper is exported and callable. We do not run it
        (no Streamlit ScriptRunContext in a unittest harness), but the
        callable surface SHALL exist and be wired through st.dialog."""

        from web.operator_ui import theme

        self.assertTrue(callable(theme.render_settings_dialog))
        self.assertTrue(callable(theme.render_topbar))
        self.assertTrue(callable(theme.render_skip_link))

        # Source-level check: the dialog uses st.dialog and persists via
        # save_preferences. This guards against accidental regression to
        # the legacy sidebar-expander pattern.
        source = Path("web/operator_ui/theme.py").read_text(encoding="utf-8")
        self.assertIn("st.dialog", source)
        self.assertIn('"Settings"', source)
        self.assertIn("save_preferences", source)


if __name__ == "__main__":
    unittest.main()
