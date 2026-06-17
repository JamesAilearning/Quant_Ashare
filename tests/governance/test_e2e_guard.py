"""Governance test for the E2E gate's flag parsing.

The E2E suite hits real qlib bundles / training / backtest and has frozen this
machine in the past — it MUST stay off unless explicitly enabled, and MUST turn
on for every reasonable truthy spelling so an operator who exports ``RUN_E2E=True``
isn't silently left with it off (or, symmetrically, isn't surprised by it
running). This pins the case-insensitive parsing.
"""

from __future__ import annotations

import unittest

from tests.e2e_guard import _env_flag_enabled


class EnvFlagEnabledTests(unittest.TestCase):
    def test_canonical_on_values(self) -> None:
        for v in ("1", "true", "yes", "on"):
            with self.subTest(v=v):
                self.assertTrue(_env_flag_enabled(v))

    def test_case_insensitive(self) -> None:
        # The bug this PR fixes: these were treated as OFF before .lower().
        for v in ("True", "TRUE", "Yes", "YES", "On", "ON"):
            with self.subTest(v=v):
                self.assertTrue(_env_flag_enabled(v))

    def test_surrounding_whitespace_tolerated(self) -> None:
        self.assertTrue(_env_flag_enabled("  true  "))
        self.assertTrue(_env_flag_enabled("\t1\n"))

    def test_off_values(self) -> None:
        for v in ("0", "false", "no", "off", "", "  ", "maybe", "2"):
            with self.subTest(v=v):
                self.assertFalse(_env_flag_enabled(v))

    def test_none_is_off(self) -> None:
        # os.environ.get(...) returns None when the var is unset.
        self.assertFalse(_env_flag_enabled(None))


if __name__ == "__main__":
    unittest.main()
