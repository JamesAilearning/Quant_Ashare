"""Tests for the walk-forward CLI config loader."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.run_walk_forward import _load_config  # noqa: E402
from src.core.canonical_backtest_contract import ADJUST_MODE_NONE  # noqa: E402


class WalkForwardCliConfigTests(unittest.TestCase):
    def test_runtime_adjust_mode_follows_walk_forward_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "walk.yaml"
            cfg.write_text(
                "\n".join(
                    [
                        'provider_uri: "D:/qlib_data/my_cn_data"',
                        'region: "cn"',
                        f'adjust_mode: "{ADJUST_MODE_NONE}"',
                    ]
                ),
                encoding="utf-8",
            )

            wf_config, qlib_config = _load_config(str(cfg))

        self.assertEqual(wf_config.adjust_mode, ADJUST_MODE_NONE)
        self.assertEqual(qlib_config.data_adjust_mode, ADJUST_MODE_NONE)

    def test_provider_uri_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "walk.yaml"
            cfg.write_text('region: "cn"\n', encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "provider_uri"):
                _load_config(str(cfg))


if __name__ == "__main__":
    unittest.main()
