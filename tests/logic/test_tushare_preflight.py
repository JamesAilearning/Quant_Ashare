"""Tests for ``scripts/tushare_preflight``.

Token discretion (length-only, never the secret) and step-by-step
PASS/FAIL behaviour are the surface this script's value rests on —
both have to keep working independently of the live Tushare API.
The full network-touching path is exercised manually by the operator;
unit tests cover everything else.
"""

from __future__ import annotations

import importlib.util
import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Load the script as a module via its file path. We deliberately avoid
# adding ``scripts/`` to sys.path globally — the test runner only needs
# this one file, and the script's own ``sys.path.insert`` call is
# idempotent.
_PREFLIGHT_PATH = PROJECT_ROOT / "scripts" / "tushare_preflight.py"
_spec = importlib.util.spec_from_file_location("tushare_preflight", _PREFLIGHT_PATH)
assert _spec is not None and _spec.loader is not None
preflight = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(preflight)  # type: ignore[union-attr]


from src.data.tushare.client import _TOKEN_ENV_VAR  # noqa: E402

# ---------------------------------------------------------------------
# _summarise_token: must never reveal the secret
# ---------------------------------------------------------------------


class SummariseTokenTests(unittest.TestCase):
    """The token preview is the only thing about the token that is
    printed; if a future edit started leaking middle bytes the failure
    would be silent. Pin the redaction.
    """

    def test_full_length_token_shows_head_and_tail_only(self) -> None:
        token = "abcdef" + "X" * 30 + "789f"
        out = preflight._summarise_token(token)
        self.assertIn("len=40", out)
        self.assertIn("abcdef", out)
        self.assertIn("789f", out)
        # The middle 30 X's must NOT leak.
        self.assertNotIn("XXX", out)

    def test_short_string_does_not_index_out_of_bounds(self) -> None:
        # Defensive: a malformed env value (3 chars) must not raise.
        out = preflight._summarise_token("abc")
        self.assertIn("len=3", out)
        # And the redaction must flag the value as too short — a
        # silent pass would hide a setup mistake.
        self.assertIn("too short", out.lower())

    def test_empty_string_handled(self) -> None:
        out = preflight._summarise_token("")
        self.assertIn("len=0", out)
        self.assertIn("empty", out.lower())


# ---------------------------------------------------------------------
# Step 1: missing TUSHARE_TOKEN must short-circuit with exit code 1
# ---------------------------------------------------------------------


class MissingTokenTests(unittest.TestCase):
    def _run_with_env(self, env_overrides: dict) -> tuple[int, str]:
        with patch.dict("os.environ", env_overrides, clear=False):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = preflight._run_preflight()
            return code, buf.getvalue()

    def test_unset_env_var_returns_1(self) -> None:
        env_without = {
            k: v for k, v in __import__("os").environ.items()
            if k != _TOKEN_ENV_VAR
        }
        with patch.dict("os.environ", env_without, clear=True):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = preflight._run_preflight()
        self.assertEqual(code, 1)
        # Output must name the env var so the operator sees what to set.
        self.assertIn(_TOKEN_ENV_VAR, buf.getvalue())
        # Output must surface the FAIL on Step 1.
        self.assertIn("FAIL", buf.getvalue())
        self.assertIn("Step 1/5", buf.getvalue())

    def test_empty_env_var_returns_1(self) -> None:
        code, output = self._run_with_env({_TOKEN_ENV_VAR: ""})
        self.assertEqual(code, 1)
        self.assertIn("FAIL", output)

    def test_whitespace_only_env_var_returns_1(self) -> None:
        code, output = self._run_with_env({_TOKEN_ENV_VAR: "   "})
        self.assertEqual(code, 1)
        self.assertIn("FAIL", output)


# ---------------------------------------------------------------------
# Step 2: tushare missing must short-circuit *after* Step 1 passes
# ---------------------------------------------------------------------


class MissingTushareModuleTests(unittest.TestCase):
    """If the operator forgot ``pip install tushare`` the preflight
    must say so, not crash with a generic ImportError stack."""

    def test_missing_tushare_returns_1_with_install_hint(self) -> None:
        # Hide any installed tushare so the preflight's import fails.
        with patch.dict("os.environ", {_TOKEN_ENV_VAR: "abcdef" * 6}), \
             patch.dict("sys.modules", {"tushare": None}):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = preflight._run_preflight()
        output = buf.getvalue()
        self.assertEqual(code, 1)
        self.assertIn("Step 2/5", output)
        self.assertIn("FAIL", output)
        self.assertIn(".[tushare]", output)


# ---------------------------------------------------------------------
# Token never leaks via stdout, even on the success path
# ---------------------------------------------------------------------


class TokenDoesNotLeakTests(unittest.TestCase):
    """Independently of Step 2's outcome, the token bytes must never
    appear in stdout. We force Step 2 to fail (no tushare) so we don't
    need a working API; the token disclosure happens earlier in Step 1.
    """

    def test_full_token_string_absent_from_output(self) -> None:
        secret = "ABCDEF" + "S" * 28 + "WXYZ"  # 38 chars
        with patch.dict("os.environ", {_TOKEN_ENV_VAR: secret}), \
             patch.dict("sys.modules", {"tushare": None}):
            buf = io.StringIO()
            with redirect_stdout(buf):
                preflight._run_preflight()
        output = buf.getvalue()
        # Length appears (we want that).
        self.assertIn("len=38", output)
        # Head/tail snippets appear.
        self.assertIn("ABCDEF", output)
        self.assertIn("WXYZ", output)
        # The middle "SSSS..." block must NOT appear anywhere.
        self.assertNotIn("S" * 10, output)
        # And the full token string must not be present in one piece.
        self.assertNotIn(secret, output)


if __name__ == "__main__":
    unittest.main()
