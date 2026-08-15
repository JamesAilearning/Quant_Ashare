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

import contextlib
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
# Fields whose value comes from a module-level resolver rather than a
# class attribute. Both the dataclass factory and the CLI read it, which
# is what makes them single-source — so that function is what a linkage
# test has to move.
_FACTORY_RESOLVERS = {"name_source_parquet": "default_name_source"}

_ALIASES = {"st_max_age_days": "st_snapshot_max_age_days",
            "name_source": "name_source_parquet"}

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
        """Every field that HAS a default — plain or ``default_factory``.

        The first cut took only plain defaults, so the one factory field
        (``name_source_parquet``) was invisible and the CLI's own
        ``_DEFAULT_NAME_SOURCE`` literal — a fourth copy that already existed
        — sailed straight through a sweep advertised as closing the class
        (codex #438 r1). "Has a default" is the property; how it is spelled
        is not.
        """
        out: dict[str, object] = {}
        for f in dataclasses.fields(RecommendationConfig):
            if f.default is not dataclasses.MISSING:
                out[f.name] = f.default
            elif f.default_factory is not dataclasses.MISSING:
                out[f.name] = f.default_factory()
        return out

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

    def test_moving_the_config_moves_every_linked_cli_default(self) -> None:
        """Equality alone is satisfiable by two literals that happen to match.

        Move the dataclass value and require the parser to follow — the only
        assertion that distinguishes "reads the config" from "restates it".

        Over EVERY linked pair, not one of them: the first cut moved only
        ``bundle_max_age_days``, so ``--out-dir`` and ``--st-max-age-days``
        could each regress to a competing literal and stay green, since the
        equality sweep cannot tell two agreeing literals from one source
        (codex #438 r1).
        """
        parser = _cli_module()._build_arg_parser()  # type: ignore[attr-defined]
        pairs = self._linked_pairs(parser)
        self.assertGreaterEqual(len(pairs), 4, pairs)
        for dest, field in pairs:
            with self.subTest(dest=dest, field=field):
                moved = self._moved_value(self._config_defaults()[field])
                with self._config_value_moved(field, moved):
                    got = getattr(
                        _cli_module()._build_arg_parser().parse_args([]),  # type: ignore[attr-defined]
                        dest)
                self.assertEqual(
                    moved, got,
                    f"挪动 RecommendationConfig.{field} 的来源后 --"
                    f"{dest.replace('_', '-')} 没有跟随:它仍在复述字面量")

    @contextlib.contextmanager
    def _config_value_moved(self, field: str, moved: object):
        """Move the value AT ITS SOURCE — which differs by field kind.

        A plain default lives on the class, so the class attribute is the
        source. A ``default_factory`` field does NOT: its value comes from
        the resolver the factory delegates to, and the CLI reads that same
        resolver. Setting the class attribute there would move nothing and
        the test would fail against a correctly-wired implementation — the
        mutation has to match how the value is actually sourced, or it pins
        the wrong thing (found by this test going red on `--name-source`).
        """
        resolver = _FACTORY_RESOLVERS.get(field)
        if resolver is not None:
            import src.inference.daily_recommend as serving
            saved = getattr(serving, resolver)
            setattr(serving, resolver, lambda: moved)
            try:
                yield
            finally:
                setattr(serving, resolver, saved)
            return
        saved_attr = getattr(RecommendationConfig, field)
        setattr(RecommendationConfig, field, moved)
        try:
            yield
        finally:
            setattr(RecommendationConfig, field, saved_attr)

    @staticmethod
    def _moved_value(current: object) -> object:
        if isinstance(current, bool):
            return not current
        if isinstance(current, int):
            return current + 5
        return f"{current}__MOVED"

    def _linked_pairs(self, parser: object) -> list[tuple[str, str]]:
        config_defaults = self._config_defaults()
        found: list[tuple[str, str]] = []
        for action in parser._actions:  # type: ignore[attr-defined]
            field = _ALIASES.get(action.dest, action.dest)
            if field in config_defaults and field not in _RESOLVED_ELSEWHERE:
                found.append((action.dest, field))
        return found


if __name__ == "__main__":
    unittest.main()
