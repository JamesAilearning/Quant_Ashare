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
        # Match a hex literal *inside quotes* — the form actually used for
        # color strings (``"#ffaa00"``, ``'#fff'``, etc.).  Bare ``#NNN``
        # tokens in comments / docstrings (PR refs, issue numbers, anchor
        # links) are not color literals and SHALL NOT trip this guard.
        pattern = re.compile(r"""['"]#[0-9A-Fa-f]{3,8}['"]""")

        offenders: list[str] = []
        for path in Path("web/operator_ui").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if pattern.search(text):
                offenders.append(str(path))

        self.assertEqual(offenders, [], "Python UI sources should use CSS tokens")

    def test_app_registers_design_system_page_behind_env_gate(self) -> None:
        """Theme injection must still happen unconditionally. The
        design-system demo page (token swatches + component gallery
        for visual QA) MUST be gated behind ``QV2_SHOW_DESIGN_SYSTEM``
        so it doesn't show up in the production operator menu and
        get misread as a real settings page (UI review P1-12)."""

        source = Path("web/operator_ui/app.py").read_text(encoding="utf-8")

        self.assertIn("inject_theme", source)
        # File reference still present (just gated).
        self.assertIn("design_system.py", source)
        # The env var that operators opt into for design QA.
        self.assertIn("QV2_SHOW_DESIGN_SYSTEM", source)
        # Defence-in-depth: the design_system Page literal MUST sit
        # inside an env-var conditional, not at the top of the nav
        # dict.
        ds_idx = source.index('design_system.py')
        gate_idx = source.index('QV2_SHOW_DESIGN_SYSTEM')
        self.assertLess(
            gate_idx, ds_idx,
            "QV2_SHOW_DESIGN_SYSTEM check must appear before "
            "design_system.py is added to the navigation",
        )

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

    def test_skip_link_html_contains_link_but_not_target_anchor(self) -> None:
        """``SKIP_LINK_HTML`` now emits ONLY the link; the matching
        ``<a id="qv2-main-content">`` target moved into
        ``page_header.render_page_header`` (UI review P0-4) so that
        pressing Enter on the skip link actually lands past the topbar /
        sidebar / breadcrumb instead of just below the link itself."""

        from web.operator_ui.theme import SKIP_LINK_HTML, SKIP_LINK_TARGET_ID

        self.assertEqual(SKIP_LINK_TARGET_ID, "qv2-main-content")
        self.assertIn(f'href="#{SKIP_LINK_TARGET_ID}"', SKIP_LINK_HTML)
        self.assertIn("qv2-skip-link", SKIP_LINK_HTML)
        # The target anchor MUST NOT live alongside the link — that
        # placement is exactly the bug P0-4 fixed.
        self.assertNotIn(
            f'id="{SKIP_LINK_TARGET_ID}"',
            SKIP_LINK_HTML,
            "Skip-link target must live in page_header, not adjacent to the link",
        )

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
        self.assertIn('"设置"', source)
        self.assertIn("save_preferences", source)

    def test_reset_settings_dialog_state_clears_all_widget_keys(self) -> None:
        """Save AND Cancel SHALL drop the dialog's transient widget keys
        from ``st.session_state`` so the next open rehydrates from
        persisted preferences (regression guard for Codex PR #116 P2)."""

        import sys
        import types

        from web.operator_ui import theme

        # Inject a minimal fake streamlit so _reset_settings_dialog_state
        # can pop keys from a session_state-like mapping without needing a
        # real ScriptRunContext.
        fake_state: dict[str, object] = {
            "qv2_settings_theme": "dark",
            "qv2_settings_color_convention": "western",
            "qv2_settings_sidebar_collapsed": True,
            "unrelated_key": "kept",
        }
        fake_st = types.SimpleNamespace(session_state=fake_state)
        original = sys.modules.get("streamlit")
        sys.modules["streamlit"] = fake_st  # type: ignore[assignment]
        try:
            theme._reset_settings_dialog_state()
        finally:
            if original is not None:
                sys.modules["streamlit"] = original
            else:
                sys.modules.pop("streamlit", None)

        self.assertNotIn("qv2_settings_theme", fake_state)
        self.assertNotIn("qv2_settings_color_convention", fake_state)
        self.assertNotIn("qv2_settings_sidebar_collapsed", fake_state)
        self.assertEqual(fake_state.get("unrelated_key"), "kept")

    def test_render_settings_dialog_resets_on_both_save_and_cancel(self) -> None:
        """Source-level guard: every exit path from the dialog SHALL call
        ``_reset_settings_dialog_state`` so closed dialog state never
        leaks into the next opening (Codex PR #116 P2)."""

        source = Path("web/operator_ui/theme.py").read_text(encoding="utf-8")

        # Locate the dialog body and look for the reset call near both
        # ``save_clicked`` and ``cancel_clicked`` branches.
        self.assertIn("_reset_settings_dialog_state", source)
        save_idx = source.index("if save_clicked:")
        cancel_idx = source.index("elif cancel_clicked:")
        end_idx = source.index("_dialog()", cancel_idx)
        save_block = source[save_idx:cancel_idx]
        cancel_block = source[cancel_idx:end_idx]
        self.assertIn(
            "_reset_settings_dialog_state",
            save_block,
            "Save path must reset dialog widget state",
        )
        self.assertIn(
            "_reset_settings_dialog_state",
            cancel_block,
            "Cancel path must reset dialog widget state",
        )

    def test_render_topbar_emits_marker_and_decorator_script(self) -> None:
        """``render_topbar`` SHALL emit the host marker plus a JS snippet
        that tags the enclosing container with ``.qv2-topbar``,
        ``data-qv2-topbar-host="true"`` and the trailing column with
        ``.qv2-topbar-actions`` so the shell CSS actually applies
        (Codex PR #116 P2 — CSS-without-host bug)."""

        source = Path("web/operator_ui/theme.py").read_text(encoding="utf-8")

        self.assertIn("qv2-topbar-host-marker", source)
        self.assertIn('data-qv2-topbar-host', source)
        self.assertIn("qv2-topbar-actions", source)
        # The decorator script SHALL look at stVerticalBlock + stColumn
        # so it targets Streamlit's actual DOM, not arbitrary nodes.
        self.assertIn("stVerticalBlock", source)
        self.assertIn("stColumn", source)

    def test_topbar_tagging_uses_mutation_observer_not_polling(self) -> None:
        """UI review P2-3: the topbar host-tagging script must re-apply
        on DOM changes via a ``MutationObserver`` (idempotent,
        install-once), not the old bounded ``setTimeout`` retry loop that
        gave up after ~1s and missed later Streamlit reruns."""

        source = Path("web/operator_ui/theme.py").read_text(encoding="utf-8")

        self.assertIn("MutationObserver", source)
        self.assertIn("__qv2TopbarObserver", source)
        self.assertIn("childList: true, subtree: true", source)
        # Polling loop + bounded-attempt counter are gone.
        self.assertNotIn("setTimeout(decorate", source)
        self.assertNotIn("attempts < 10", source)


if __name__ == "__main__":
    unittest.main()
