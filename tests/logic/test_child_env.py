"""Behavioral proof for scripts/child_env.py.

The governance gate can only check that a spawn PASSES
``env=utf8_child_env()``; whether that env actually makes a child emit
UTF-8 is a runtime question, and this is where it is answered — by
spawning a real child that prints non-ASCII and reading the bytes back.
Without this test the gate would be enforcing a spelling, not a property.
"""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.child_env import (  # noqa: E402
    CHILD_ENCODING,
    CHILD_ENCODING_VAR,
    utf8_child_env,
)

# An em dash + Chinese: the two shapes this repo's refusal messages and
# log lines actually carry, and the ones cp936 mangles or refuses.
_NON_ASCII = "refusal — 中文"
_CHILD = f'print({_NON_ASCII!r})'


class Utf8ChildEnvTests(unittest.TestCase):
    def test_pins_the_encoder_variable(self) -> None:
        env = utf8_child_env()
        self.assertEqual(env[CHILD_ENCODING_VAR], CHILD_ENCODING)

    def test_preserves_the_rest_of_the_environment(self) -> None:
        env = utf8_child_env()
        for key, value in os.environ.items():
            if key != CHILD_ENCODING_VAR:
                self.assertEqual(env.get(key), value, key)

    def test_overrides_a_hostile_inherited_value(self) -> None:
        # The pin is an assignment AFTER the copy, so a pre-existing
        # cp936 setting cannot survive — the merge-order trap that makes
        # `{"PYTHONIOENCODING": "utf-8", **os.environ}` a false pin.
        env = utf8_child_env({CHILD_ENCODING_VAR: "cp936", "KEEP": "1"})
        self.assertEqual(env[CHILD_ENCODING_VAR], CHILD_ENCODING)
        self.assertEqual(env["KEEP"], "1")

    def test_does_not_mutate_the_caller_mapping(self) -> None:
        base = {"KEEP": "1"}
        utf8_child_env(base)
        self.assertNotIn(CHILD_ENCODING_VAR, base)

    def test_real_child_round_trips_non_ascii(self) -> None:
        # THE point of the module: parent decodes as UTF-8, so the child
        # must ENCODE as UTF-8 — which only the env makes true.
        proc = subprocess.run(
            [sys.executable, "-c", _CHILD],
            capture_output=True, text=True, encoding="utf-8",
            env=utf8_child_env(), timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), _NON_ASCII)

    def test_child_round_trip_survives_a_hostile_locale_setting(self) -> None:
        # Simulate the CN Windows box even on a UTF-8 CI runner: an
        # inherited cp936 pin must be overridden, not merged around.
        hostile = {**os.environ, CHILD_ENCODING_VAR: "cp936"}
        proc = subprocess.run(
            [sys.executable, "-c", _CHILD],
            capture_output=True, text=True, encoding="utf-8",
            env=utf8_child_env(hostile), timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), _NON_ASCII)


if __name__ == "__main__":
    unittest.main()
