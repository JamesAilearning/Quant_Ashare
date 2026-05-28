"""Source-level regression guards for the Tushare ingest page."""

from __future__ import annotations

import unittest
from pathlib import Path


class TushareReuseStagedDefaultTests(unittest.TestCase):
    """``reuse_staged`` default flipped from True → False (UI review P1-8).

    The old default silently fed downstream qlib bin builder whatever
    Parquet happened to be on disk in the staging directory. A prior
    crash (Tushare rate limit, network blip, operator Ctrl+C) left
    half-downloaded files that the next "拉取" reused without any
    visible signal, polluting training. Defaulting to off forces the
    operator to opt in to reuse and surfaces a warning when they do.
    """

    def setUp(self) -> None:
        self.source = Path("web/operator_ui/pages/tushare.py").read_text(
            encoding="utf-8"
        )

    def test_reuse_staged_checkbox_defaults_to_false(self) -> None:
        # Scope the assertion to the checkbox call site so an unrelated
        # ``value=False`` elsewhere in the file cannot mask a regression.
        marker = '"复用已暂存的 Parquet (reuse_staged)"'
        self.assertIn(marker, self.source)
        idx = self.source.index(marker)
        window = self.source[idx: idx + 600]
        self.assertIn("value=False", window)
        self.assertNotIn("value=True", window)

    def test_reuse_staged_help_text_warns_about_silent_reuse(self) -> None:
        """When the operator hovers the checkbox label, the help text
        SHALL explain why the default is off (unverified prior downloads
        can pollute training)."""

        # Tightly tied to the warning copy so a refactor that removes
        # the explanation gets caught.
        self.assertIn("每次都重新拉取最新数据", self.source)
        self.assertIn("不完整 Parquet", self.source)

    def test_post_submit_banner_warns_when_reuse_staged_enabled(self) -> None:
        """After job submission, a visible ``st.warning`` MUST surface
        when ``reuse_staged`` was enabled — operators need a reminder
        that the staging files were NOT re-validated by the UI."""

        self.assertIn("if reuse_staged:", self.source)
        self.assertIn("st.warning(", self.source)
        self.assertIn("未重新校验", self.source)


class TushareTokenSecrecyStillHoldsTests(unittest.TestCase):
    """Belt-and-braces re-check that the P1-8 default flip didn't
    weaken the existing secrets policy: the token still gates rendering
    and never appears in any persisted state."""

    def setUp(self) -> None:
        self.source = Path("web/operator_ui/pages/tushare.py").read_text(
            encoding="utf-8"
        )

    def test_token_is_only_read_for_presence_check(self) -> None:
        self.assertIn('os.environ.get("TUSHARE_TOKEN"', self.source)
        # The page MUST NOT echo the token into the YAML or any visible
        # text rendering surface.
        self.assertNotIn("st.code(", self.source)


if __name__ == "__main__":
    unittest.main()
