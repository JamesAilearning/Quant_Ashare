"""Theme token injection and persisted appearance preferences for the UI."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from web.operator_ui._path_guard import output_path

ThemeMode = Literal["auto", "light", "dark"]
ColorConvention = Literal["chinese", "western"]

THEME_OPTIONS: tuple[ThemeMode, ...] = ("auto", "light", "dark")
COLOR_CONVENTION_OPTIONS: tuple[ColorConvention, ...] = ("chinese", "western")
THEME_STORAGE_KEY = "qv2.theme"
COLOR_CONVENTION_STORAGE_KEY = "qv2.colorConvention"
SERVER_THEME_STORAGE_KEY = "qv2.serverTheme"
SERVER_COLOR_CONVENTION_STORAGE_KEY = "qv2.serverColorConvention"
STATIC_DIR = Path(__file__).resolve().parent / "static"
THEME_CSS_PATH = STATIC_DIR / "theme.css"
PREFERENCES_PATH = output_path("operator_ui", "preferences.json")


@dataclass(frozen=True)
class UserPreferences:
    """Presentation-only preferences for the operator UI shell."""

    theme: ThemeMode = "auto"
    color_convention: ColorConvention = "chinese"
    sidebar_collapsed: bool = False

    @classmethod
    def from_mapping(cls, values: object) -> UserPreferences:
        if not isinstance(values, dict):
            return cls()
        theme = values.get("theme")
        color_convention = values.get("color_convention")
        sidebar_collapsed = values.get("sidebar_collapsed", False)
        return cls(
            theme=theme if theme in THEME_OPTIONS else "auto",
            color_convention=(
                color_convention
                if color_convention in COLOR_CONVENTION_OPTIONS
                else "chinese"
            ),
            sidebar_collapsed=(
                sidebar_collapsed if isinstance(sidebar_collapsed, bool) else False
            ),
        )

    def to_json_dict(self) -> dict[str, str | bool]:
        return {
            "theme": self.theme,
            "color_convention": self.color_convention,
            "sidebar_collapsed": self.sidebar_collapsed,
        }


def load_preferences(path: Path = PREFERENCES_PATH) -> UserPreferences:
    """Load persisted presentation preferences, falling back to defaults."""

    if not path.is_file():
        return UserPreferences()
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return UserPreferences()
    return UserPreferences.from_mapping(loaded)


def save_preferences(
    preferences: UserPreferences,
    path: Path = PREFERENCES_PATH,
) -> None:
    """Persist presentation preferences under the operator UI output tree."""

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f"{path.suffix}.tmp")
    tmp.write_text(
        json.dumps(preferences.to_json_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(tmp, path)


def load_theme_css(path: Path = THEME_CSS_PATH) -> str:
    """Read the centralized token CSS file."""

    return path.read_text(encoding="utf-8")


def preference_attribute_script(preferences: UserPreferences) -> str:
    """Return a small script that applies theme attributes to the document.

    When ``theme`` is ``"auto"`` the script detects the OS color-scheme
    synchronously before first paint and listens for runtime changes.
    """

    theme = json.dumps(preferences.theme)
    convention = json.dumps(preferences.color_convention)
    theme_options = json.dumps(THEME_OPTIONS)
    convention_options = json.dumps(COLOR_CONVENTION_OPTIONS)
    theme_storage_key = json.dumps(THEME_STORAGE_KEY)
    convention_storage_key = json.dumps(COLOR_CONVENTION_STORAGE_KEY)
    server_theme_storage_key = json.dumps(SERVER_THEME_STORAGE_KEY)
    server_convention_storage_key = json.dumps(SERVER_COLOR_CONVENTION_STORAGE_KEY)
    return f"""
<script>
(function() {{
  var root = window.parent.document.documentElement;
  var themeOptions = {theme_options};
  var conventionOptions = {convention_options};
  var themeStorageKey = {theme_storage_key};
  var conventionStorageKey = {convention_storage_key};
  var serverThemeStorageKey = {server_theme_storage_key};
  var serverConventionStorageKey = {server_convention_storage_key};
  var fallbackTheme = {theme};
  var fallbackConvention = {convention};

  function safeGet(key) {{
    try {{
      return window.parent.localStorage.getItem(key);
    }} catch (e) {{
      return null;
    }}
  }}

  function safeSet(key, value) {{
    try {{
      window.parent.localStorage.setItem(key, value);
    }} catch (e) {{}}
  }}

  function supported(value, options, fallback) {{
    return options.indexOf(value) >= 0 ? value : fallback;
  }}

  var previousServerTheme = safeGet(serverThemeStorageKey);
  var previousServerConvention = safeGet(serverConventionStorageKey);
  var serverPreferenceChanged = (
    (previousServerTheme !== null && previousServerTheme !== fallbackTheme) ||
    (
      previousServerConvention !== null &&
      previousServerConvention !== fallbackConvention
    )
  );
  var theme = fallbackTheme;
  var convention = fallbackConvention;
  if (!serverPreferenceChanged) {{
    theme = supported(safeGet(themeStorageKey), themeOptions, fallbackTheme);
    convention = supported(
      safeGet(conventionStorageKey),
      conventionOptions,
      fallbackConvention
    );
  }}

  function applyTheme(t) {{
    root.setAttribute("data-theme", t);
    root.setAttribute("data-qv2-theme", t);
  }}

  if (theme === "auto" && window.matchMedia) {{
    var mq = window.matchMedia("(prefers-color-scheme: dark)");
    applyTheme(mq.matches ? "dark" : "light");
    mq.addEventListener("change", function(e) {{
      applyTheme(e.matches ? "dark" : "light");
    }});
  }} else {{
    applyTheme(theme);
  }}

  root.setAttribute("data-color-convention", convention);
  root.setAttribute("data-qv2-color-convention", convention);
  safeSet(themeStorageKey, theme);
  safeSet(conventionStorageKey, convention);
  safeSet(serverThemeStorageKey, fallbackTheme);
  safeSet(serverConventionStorageKey, fallbackConvention);

  window.parent.qv2SetAppearancePreference = function(nextTheme, nextConvention) {{
    var resolvedTheme = supported(nextTheme, themeOptions, theme);
    var resolvedConvention = supported(nextConvention, conventionOptions, convention);
    safeSet(themeStorageKey, resolvedTheme);
    safeSet(conventionStorageKey, resolvedConvention);
    safeSet(serverThemeStorageKey, resolvedTheme);
    safeSet(serverConventionStorageKey, resolvedConvention);
    applyTheme(resolvedTheme);
    root.setAttribute("data-color-convention", resolvedConvention);
    root.setAttribute("data-qv2-color-convention", resolvedConvention);
  }};
}})();
</script>
"""


def inject_theme(preferences: UserPreferences | None = None) -> UserPreferences:
    """Inject design-token CSS and apply persisted appearance preferences."""

    import streamlit as st

    current = preferences or load_preferences()
    st.markdown(f"<style>{load_theme_css()}</style>", unsafe_allow_html=True)
    st.html(preference_attribute_script(current), width="content", unsafe_allow_javascript=True)
    return current


def render_appearance_controls(preferences: UserPreferences) -> UserPreferences:
    """Render sidebar appearance controls — kept for compatibility.

    New code should prefer :func:`render_settings_dialog`, which surfaces the
    same preferences inside a modal launched from the topbar.  This helper is
    retained so tests and embeds that import it keep working.
    """

    import streamlit as st

    with st.sidebar.expander("Appearance", expanded=False):
        theme = st.radio(
            "Theme",
            options=THEME_OPTIONS,
            index=THEME_OPTIONS.index(preferences.theme),
            horizontal=True,
            key="qv2_theme_mode",
        )
        color_convention = st.radio(
            "Color convention",
            options=COLOR_CONVENTION_OPTIONS,
            index=COLOR_CONVENTION_OPTIONS.index(preferences.color_convention),
            horizontal=True,
            key="qv2_color_convention",
            help="Chinese convention uses red for positive outcomes.",
        )
    updated = UserPreferences(
        theme=theme,
        color_convention=color_convention,
        sidebar_collapsed=preferences.sidebar_collapsed,
    )
    if updated != preferences:
        save_preferences(updated)
    return updated


# ---------------------------------------------------------------------------
# App shell helpers — skip link, topbar, settings dialog
# ---------------------------------------------------------------------------

SKIP_LINK_HTML = (
    '<a class="qv2-skip-link" href="#qv2-main-content">Skip to content</a>'
    '<a id="qv2-main-content" tabindex="-1" class="qv2-sr-only">Main content</a>'
)


def render_skip_link() -> None:
    """Inject a keyboard-accessible "skip to content" affordance.

    The link is visually hidden until focused; pressing Tab on a fresh page
    surfaces it and Enter scrolls to the main content anchor.
    """

    import streamlit as st

    st.html(SKIP_LINK_HTML, width="content", unsafe_allow_javascript=False)


def render_topbar(
    *,
    title: str = "Qlib Trading System",
    subtitle: str = "",
    on_open_settings_key: str = "qv2_open_settings",
) -> bool:
    """Render the sticky top bar.

    Returns ``True`` when the user clicked the settings button on this run.
    Callers (typically :mod:`web.operator_ui.app`) should open the settings
    dialog in response.
    """

    import streamlit as st

    with st.container():
        title_col, action_col = st.columns([8, 2], vertical_alignment="center")
        with title_col:
            subtitle_html = (
                f'<span class="qv2-topbar-subtitle">{subtitle}</span>'
                if subtitle
                else ""
            )
            st.markdown(
                (
                    '<div class="qv2-topbar-title">'
                    f'<span>{title}</span>{subtitle_html}'
                    "</div>"
                ),
                unsafe_allow_html=True,
            )
        with action_col:
            opened = st.button(
                "⚙️ Settings",
                key=on_open_settings_key,
                help="Theme, color convention, and sidebar defaults",
                use_container_width=True,
            )
    return bool(opened)


def render_settings_dialog(preferences: UserPreferences) -> None:
    """Open the settings modal.

    The dialog is decorated with :func:`streamlit.dialog` and shows controls
    for theme, color convention, and sidebar default state.  Changes are
    persisted to :data:`PREFERENCES_PATH` when the user clicks **Save**;
    the page is then rerun so the new tokens apply immediately.
    """

    import streamlit as st

    @st.dialog("Settings")
    def _dialog() -> None:
        st.markdown(
            '<div class="qv2-settings-section-title">Appearance</div>',
            unsafe_allow_html=True,
        )
        theme = st.radio(
            "Theme",
            options=THEME_OPTIONS,
            index=THEME_OPTIONS.index(preferences.theme),
            horizontal=True,
            key="qv2_settings_theme",
        )
        color_convention = st.radio(
            "Color convention",
            options=COLOR_CONVENTION_OPTIONS,
            index=COLOR_CONVENTION_OPTIONS.index(preferences.color_convention),
            horizontal=True,
            key="qv2_settings_color_convention",
            help="Chinese convention uses red for positive outcomes.",
        )
        st.markdown(
            '<div class="qv2-settings-section-title" '
            'style="margin-top: var(--space-3);">Layout</div>',
            unsafe_allow_html=True,
        )
        sidebar_collapsed = st.checkbox(
            "Start with sidebar collapsed",
            value=preferences.sidebar_collapsed,
            key="qv2_settings_sidebar_collapsed",
            help="Applies on the next page load.",
        )
        st.divider()
        save_col, cancel_col = st.columns(2)
        with save_col:
            save_clicked = st.button(
                "Save", key="qv2_settings_save", type="primary", use_container_width=True
            )
        with cancel_col:
            cancel_clicked = st.button(
                "Cancel", key="qv2_settings_cancel", use_container_width=True
            )
        if save_clicked:
            updated = UserPreferences(
                theme=theme,
                color_convention=color_convention,
                sidebar_collapsed=sidebar_collapsed,
            )
            if updated != preferences:
                save_preferences(updated)
            st.rerun()
        elif cancel_clicked:
            st.rerun()

    _dialog()
