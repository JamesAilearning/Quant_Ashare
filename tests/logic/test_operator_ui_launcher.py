"""Unit tests for the Streamlit launcher boundary."""

from __future__ import annotations

import sys as _sys
import unittest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_PROJECT_ROOT))


class OperatorUiLauncherTests(unittest.TestCase):
    def test_launcher_defaults_to_loopback_address(self) -> None:
        from scripts.run_ui import _DEFAULT_SERVER_ADDRESS, _SERVER_ADDRESS_FLAG, _streamlit_args

        args = _streamlit_args(["--server.port", "8502"])

        address_index = args.index(_SERVER_ADDRESS_FLAG)
        self.assertEqual(args[address_index + 1], _DEFAULT_SERVER_ADDRESS)
        self.assertIn("--server.port", args)

    def test_launcher_respects_explicit_server_address(self) -> None:
        from scripts.run_ui import _SERVER_ADDRESS_FLAG, _streamlit_args

        args = _streamlit_args([f"{_SERVER_ADDRESS_FLAG}=0.0.0.0", "--server.port", "8502"])

        self.assertNotIn(_SERVER_ADDRESS_FLAG, args)
        self.assertIn(f"{_SERVER_ADDRESS_FLAG}=0.0.0.0", args)


if __name__ == "__main__":
    unittest.main()
