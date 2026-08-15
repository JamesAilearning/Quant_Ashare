"""Governance: no CLI flag may restate a ``RecommendationConfig`` default.

``scripts/daily_recommend.py`` builds a ``RecommendationConfig`` and passes
every one of these flags straight into it. So on the CLI path the dataclass
default **never applies** — the argparse default is the effective value, and
an edit to the dataclass would silently fail to take effect for every
production run. FOUR flags carried a duplicated literal — ``--out-dir``,
``--st-max-age-days``, ``--bundle-max-age-days``, and ``--name-source``
(whose copy hid behind a ``default_factory`` and survived the first sweep
that claimed to close the class); the ops cockpit had to print
``--bundle-max-age-days`` explicitly to work around exactly that divergence
(#431 r14).

This does NOT enumerate those four. It walks the parser and requires
agreement for **every** flag whose destination corresponds to a config field,
so a fifth one added later is caught the day it appears rather than the day
someone changes the dataclass and wonders why nothing moved.
"""

from __future__ import annotations

import contextlib
import dataclasses
import importlib.util
import re
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

_ALIASES = {
    "st_max_age_days": "st_snapshot_max_age_days",
    "name_source": "name_source_parquet",
    # These two were MISSING, so `--as-of` / `--ensemble-manifest` were
    # dropped at the "is this a config field?" step and their entries in
    # _RESOLVED_ELSEWHERE below were never consulted — dead exemptions that
    # made the list look like a deliberate decision when it was silently
    # covering nothing (codex #438 r3). The reachability test keeps that
    # from recurring.
    "as_of": "as_of_date",
    "ensemble_manifest": "ensemble_manifest_path",
    # Required config fields (no default), so the sweep skips them either
    # way — mapped anyway so the dest<->field correspondence is complete and
    # they come under the sweep the day one of them GAINS a default.
    "model": "model_path",
    "delisted_registry": "delisted_registry_path",
}

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


# "It says default 7" is only one spelling. `Default: 7`, `(default = 7)`,
# `default is 7` are all the same claim to an operator reading --help, and a
# regression to any of them would sail past an exact, case-sensitive substring
# search — leaving the parser reading the moved value while the text advertises
# the old one, which is the very defect this pins (codex #438 r4).
_DEFAULT_CLAIM = r"default(?:\s+value)?\s*(?:[:=]|is\b)?\s*"


# The value a claim names: up to the first separator or closing bracket, with
# a trailing sentence period dropped ("(default 14; covers …)" -> "14";
# "(default output/daily_recommend)." -> "output/daily_recommend").
_CLAIMED_VALUE = r"([^\s;,)\]]+)"


def advertised_defaults(help_text: str) -> list[str]:
    """Every value this help text claims is the default, in any spelling.

    Asking "does it advertise the CURRENT value?" cannot see a help that is
    already wrong by a THIRD value — config 12, prose 7: the current value is
    not advertised, so the "is it stale?" check finds nothing and the
    "does it match?" check is skipped, and the wrong sentence survives
    (codex #438 r5). Extracting the claims and requiring each to equal the
    current default has no such blind spot.
    """
    return [m.rstrip(".") for m in re.findall(
        _DEFAULT_CLAIM + _CLAIMED_VALUE, help_text, re.IGNORECASE)]


def _advertises_default(help_text: str, value: object) -> bool:
    """Does this help text claim ``value`` is the default, in any spelling?"""
    return str(value) in advertised_defaults(help_text)


def _action_help(parser: object, dest: str) -> str:
    """The EXPANDED help of one flag — `%(default)s` already substituted.

    Scoped to the single action rather than the whole `format_help()` output:
    other flags describe their own defaults in prose (`--topk` says
    "Default: 50"), so a whole-text search can be tripped or masked by a
    sentence about a different option.
    """
    formatter = parser._get_formatter()  # type: ignore[attr-defined]
    for action in parser._actions:  # type: ignore[attr-defined]
        if action.dest == dest:
            return re.sub(r"\s+", " ", formatter._expand_help(action))
    raise AssertionError(f"no action with dest {dest!r}")


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

    def _assert_help_claims_only(
            self, rendered: str, expected: object, dest: str) -> None:
        """EVERY default this help advertises must be the current one."""
        claims = advertised_defaults(rendered)
        for claimed in claims:
            with self.subTest(dest=dest, claimed=claimed):
                self.assertEqual(
                    str(expected), claimed,
                    f"--{dest.replace('_', '-')} 的 help 广告了 "
                    f"{claimed!r},当前默认值却是 {expected!r}:"
                    f"{rendered!r}")

    def test_no_exemption_or_alias_is_dead(self) -> None:
        """An exemption that can never fire is a false claim of coverage.

        `_RESOLVED_ELSEWHERE` reads as a list of deliberate decisions. Two of
        its entries (`as_of_date`, `ensemble_manifest_path`) were unreachable
        because the parser's dest differs from the field name and the alias
        was missing — so those flags were skipped one step EARLIER, and the
        exemption documented a decision that was never taken (codex #438 r3).
        """
        parser = _cli_module()._build_arg_parser()  # type: ignore[attr-defined]
        mapped = {_ALIASES.get(a.dest, a.dest) for a in parser._actions}
        for field in sorted(_RESOLVED_ELSEWHERE):
            with self.subTest(exemption=field):
                self.assertIn(
                    field, mapped,
                    f"_RESOLVED_ELSEWHERE 里的 {field!r} 对不上任何 flag:"
                    f"它是死条目,豁免了一个不存在的东西")
        field_names = {f.name for f in dataclasses.fields(RecommendationConfig)}
        dests = {a.dest for a in parser._actions}
        for dest, field in sorted(_ALIASES.items()):
            with self.subTest(alias=f"{dest}->{field}"):
                self.assertIn(dest, dests, f"别名左侧 {dest!r} 不是任何 flag 的 dest")
                self.assertIn(field, field_names,
                              f"别名右侧 {field!r} 不是 RecommendationConfig 的字段")

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
                # The help as it stands TODAY: a sentence that already names
                # the wrong value is wrong now, not only after something
                # moves (codex #438 r5).
                self._assert_help_claims_only(
                    _action_help(parser, action.dest),
                    config_defaults[field], action.dest)
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
                stale = self._config_defaults()[field]
                # Not every flag advertises its default in prose (--name-source
                # does not, and that is fine — an absent claim cannot go
                # stale). The rule is conditional: IF the help states a
                # default, it must state the current one.
                advertises = _advertises_default(
                    _action_help(parser, dest), stale)
                with self._config_value_moved(field, moved):
                    fresh = _cli_module()._build_arg_parser()  # type: ignore[attr-defined]
                    got = getattr(fresh.parse_args([]), dest)
                    rendered = _action_help(fresh, dest)
                self.assertEqual(
                    moved, got,
                    f"挪动 RecommendationConfig.{field} 的来源后 --"
                    f"{dest.replace('_', '-')} 没有跟随:它仍在复述字面量")
                # …and so must the OPERATOR-FACING text. The help strings
                # restated the same literals in prose, so `--help` kept
                # advertising 14 after the default moved to 21 — the flag was
                # correct and the sentence describing it was a lie
                # (codex #438 r2). Fixed with `%(default)s`, pinned here
                # rather than by grepping for that spelling: what matters is
                # that the help follows, not how it is written.
                self._assert_help_claims_only(rendered, moved, dest)
                if advertises:
                    self.assertIn(
                        str(moved), advertised_defaults(rendered),
                        f"--{dest.replace('_', '-')} 的 help 广告了默认值,"
                        f"就必须广告当前那个:{rendered!r}")

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
        # NOT a suffix of the original: `f"{current}__MOVED"` still CONTAINS
        # the stale value, so a "no longer advertises the old default" check
        # would fail against help text that moved correctly.
        return "moved/elsewhere/" + str(abs(hash(current)) % 9973)

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
