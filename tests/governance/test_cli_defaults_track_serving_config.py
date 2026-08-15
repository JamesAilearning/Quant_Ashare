"""Governance: no CLI flag may restate a ``RecommendationConfig`` default.

``scripts/daily_recommend.py`` builds a ``RecommendationConfig`` and passes
every one of these flags straight into it. So on the CLI path the dataclass
default **never applies** — the argparse default is the effective value, and
an edit to the dataclass would silently fail to take effect for every
production run. Three flags carried a duplicated literal
(``--out-dir`` / ``--st-max-age-days`` / ``--bundle-max-age-days``); the ops
cockpit had to print ``--bundle-max-age-days`` explicitly to work around
exactly that divergence (#431 r14).

This does NOT enumerate those three. It walks the parser and requires
agreement for **every** flag whose destination corresponds to a config field,
so a fourth one added later is caught the day it appears rather than the day
someone changes the dataclass and wonders why nothing moved.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import sys
import unittest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.inference.daily_recommend import RecommendationConfig  # noqa: E402

# Flags whose dest differs from the config field they feed. Kept explicit: a
# fuzzy name match would quietly stop covering a renamed field.
_ALIASES = {"st_max_age_days": "st_snapshot_max_age_days"}

# Flags that legitimately default to None/False and are resolved elsewhere
# (serving-parameter binding, or an explicit opt-in switch) rather than from
# the dataclass. Listed so the exemption is a decision, not an accident.
_RESOLVED_ELSEWHERE = {
    "instruments",              # bound from the serving params
    "topk",                     # bound from the serving params
    "rebalance_cadence_days",   # bound from the serving params
    "as_of_date",               # None = latest PIT trading day
    "allow_holey_recommend",    # store_true opt-in
    "ensemble_manifest_path",   # None = single-model mode
}


def _cli_module() -> object:
    path = _PROJECT_ROOT / "scripts" / "daily_recommend.py"
    spec = importlib.util.spec_from_file_location("_dr_cli_defaults", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class CliDefaultsTrackServingConfigTests(unittest.TestCase):
    def _config_defaults(self) -> dict[str, object]:
        return {
            f.name: f.default
            for f in dataclasses.fields(RecommendationConfig)
            if f.default is not dataclasses.MISSING
        }

    def test_no_flag_restates_a_config_default(self) -> None:
        parser = _cli_module()._build_arg_parser()  # type: ignore[attr-defined]
        config_defaults = self._config_defaults()
        checked: list[str] = []
        for action in parser._actions:
            field = _ALIASES.get(action.dest, action.dest)
            if field not in config_defaults or field in _RESOLVED_ELSEWHERE:
                continue
            checked.append(field)
            with self.subTest(flag=action.option_strings[:1], field=field):
                self.assertEqual(
                    config_defaults[field], action.default,
                    f"--{action.dest.replace('_', '-')} 的默认值与 "
                    f"RecommendationConfig.{field} 不同源:改动 dataclass 不会"
                    f"对任何 CLI 运行生效")
        # The sweep must actually be sweeping something.
        self.assertGreaterEqual(len(checked), 3, checked)

    def test_moving_the_config_moves_the_cli(self) -> None:
        """Equality alone is satisfiable by two literals that happen to match.

        Move the dataclass value and require the parser to follow — the only
        assertion that distinguishes "reads the config" from "restates it".
        """
        parser_before = _cli_module()._build_arg_parser()  # type: ignore[attr-defined]
        self.assertEqual(
            RecommendationConfig.bundle_max_age_days,
            parser_before.parse_args([]).bundle_max_age_days)
        saved = RecommendationConfig.bundle_max_age_days
        try:
            RecommendationConfig.bundle_max_age_days = saved + 5  # type: ignore[misc]
            moved = _cli_module()._build_arg_parser()  # type: ignore[attr-defined]
            self.assertEqual(saved + 5, moved.parse_args([]).bundle_max_age_days)
        finally:
            RecommendationConfig.bundle_max_age_days = saved  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
