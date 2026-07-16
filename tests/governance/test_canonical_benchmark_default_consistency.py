"""Governance (CI-runnable, no bundle): canonical benchmark default == total-return.

PR-2 promoted REGEN-2 (SH000300TR total-return) to the canonical baseline and flipped
the benchmark default across config / preset / in-code sites. This guard asserts the
SEMANTIC INVARIANTS so a NEW config OR a REVERTED flip is caught red — it deliberately
does NOT hard-list the known flip sites:

1. The canonical default benchmark — the in-code ``WalkForwardConfig`` / ``PipelineConfig``
   field defaults AND every TRACKED config YAML that declares ``benchmark_code`` — is
   drawn from the PER-UNIVERSE canonical total-return set (CSI800 expansion (b)
   Step 2: ``_CANONICAL_BENCHMARK_BY_UNIVERSE``), and a config that declares a
   universe in that map must pair with EXACTLY its universe's benchmark — a
   csi800 config on the csi300 basis (or vice versa) fails here, as does a
   csi800/csi500 config that omits ``benchmark_code`` and silently falls back to
   the in-code SH000300TR default.
2. The REGEN-A control path stays the SH000300 PRICE index: the price-replay generator
   constant + the preserved ``regen_a/`` control baseline. (REGEN-A is the price control,
   not deleted.)
3. The total-return <-> price pairing is intact: the canonical TR (SH000300TR) and the
   preserved control (SH000300) form a valid pair under the runtime's ``+TR`` suffix
   convention (``BacktestRunner`` derives ``price_code = tr_code[:-2]``), so the
   cumulative-return cross-check still pairs them.

Boundary: this is a STATIC guard — it checks the in-code dataclass defaults and every
TRACKED config YAML, so it cannot see the benchmark_code an UNTRACKED personal preset
(``my_*.yaml``) actually loads. The COMPLEMENTARY runtime check is
``BacktestRunner._warn_if_non_canonical_benchmark`` (PR-J): it warns LOUD when a run's
ACTUALLY-consumed benchmark is not the canonical total-return, so an untracked preset
left on the price index is surfaced (not blocked — the REGEN-A control legitimately
consumes SH000300). Static guard catches tracked drift; the LOAD warning surfaces the
loaded benchmark.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import yaml

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Single source of truth for the canonical benchmark = the runtime constant the
# LOAD-time check (PR-J) enforces. Importing it here pins the config defaults to the
# SAME value the runtime treats as canonical, so a drift in EITHER fails this guard.
# (backtest_runner has no top-level qlib import, so this stays a no-bundle test.)
from src.core.backtest_runner import (  # noqa: E402
    _CANONICAL_BENCHMARK_BY_UNIVERSE as CANONICAL_BY_UNIVERSE,
)
from src.core.backtest_runner import (
    _CANONICAL_BENCHMARK_CODE as CANONICAL_TR,
)

REGEN_A_PRICE = "SH000300"
REGEN_A_CONTROL_FIXTURE = (
    _PROJECT_ROOT / "tests" / "regression" / "fixtures" / "regen_a"
    / "walk_forward_baseline_metrics_regen_a.json"
)


def _tracked_config_yamls() -> list[Path]:
    """All tracked config YAMLs, EXCLUDING gitignored personal overrides (my_* / *.local.*)."""
    paths: list[Path] = []
    paths.extend(_PROJECT_ROOT.glob("config*.yaml"))
    paths.extend((_PROJECT_ROOT / "config" / "presets").glob("*.yaml"))
    return sorted(
        p for p in paths
        if not p.name.startswith("my_") and ".local." not in p.name
    )


class CanonicalBenchmarkDefaultConsistencyTests(unittest.TestCase):
    def test_incode_dataclass_defaults_are_total_return(self) -> None:
        from src.core.pipeline import PipelineConfig
        from src.core.walk_forward.config import WalkForwardConfig
        for cls in (WalkForwardConfig, PipelineConfig):
            default = cls.__dataclass_fields__["benchmark_code"].default
            self.assertEqual(
                CANONICAL_TR, default,
                f"{cls.__name__}.benchmark_code default is {default!r}, not the canonical "
                f"total-return {CANONICAL_TR!r}. A reverted in-code default leaks the price "
                "index into every config that omits benchmark_code.",
            )

    def test_per_universe_mapping_invariants(self) -> None:
        # The mapping itself is load-bearing (runtime warning + this guard both
        # consume it): every value is a TR-suffixed total-return code, and the
        # csi300 / "all" entries stay pinned to the REGEN-2 canonical constant —
        # re-pointing either is a basis change that needs its own REGEN, not a
        # mapping edit.
        for universe, bc in CANONICAL_BY_UNIVERSE.items():
            self.assertTrue(
                bc.endswith("TR"),
                f"per-universe canonical benchmark for {universe!r} is {bc!r}, "
                "not a total-return ('TR'-suffixed) code.",
            )
        for pinned in ("csi300", "all"):
            self.assertEqual(
                CANONICAL_TR, CANONICAL_BY_UNIVERSE.get(pinned),
                f"the {pinned!r} universe must stay on the REGEN-2 canonical "
                f"{CANONICAL_TR!r}; re-pointing it is a basis change requiring "
                "its own REGEN, not a mapping edit.",
            )

    def test_every_tracked_config_yaml_benchmark_in_canonical_set(self) -> None:
        # Scans ALL tracked config YAMLs (not a hard-list): a NEW config with a price
        # benchmark, or a reverted flip in any existing one, is caught here. The
        # accepted set is the per-universe canonical total-return set ((b) Step 2)
        # — universe<->benchmark PAIRING is the next test's job.
        canonical_set = set(CANONICAL_BY_UNIVERSE.values())
        offenders: list[str] = []
        for path in _tracked_config_yamls():
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                continue
            bc = data.get("benchmark_code") if isinstance(data, dict) else None
            if bc is not None and bc not in canonical_set:
                offenders.append(f"{path.relative_to(_PROJECT_ROOT).as_posix()}={bc!r}")
        self.assertEqual(
            [], offenders,
            "tracked config(s) declare a benchmark_code outside the per-universe "
            f"canonical total-return set {sorted(canonical_set)} (the SH000300 "
            "price index is the REGEN-A control only): " + ", ".join(offenders),
        )

    def test_tracked_config_universe_benchmark_pairing(self) -> None:
        # (b) Step 2: a config that declares a universe in the canonical map must
        # measure excess against EXACTLY that universe's benchmark. The EFFECTIVE
        # benchmark is the declared one, or the in-code default when omitted — so
        # a csi800/csi500 config that omits benchmark_code and silently falls
        # back to SH000300TR is an offender too, not a pass-through.
        offenders: list[str] = []
        for path in _tracked_config_yamls():
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                continue
            if not isinstance(data, dict):
                continue
            universe = data.get("instruments")
            if not isinstance(universe, str):
                continue
            expected = CANONICAL_BY_UNIVERSE.get(universe)
            if expected is None:
                continue  # universe outside the map: set-membership test covers it
            effective = data.get("benchmark_code") or CANONICAL_TR
            if effective != expected:
                offenders.append(
                    f"{path.relative_to(_PROJECT_ROOT).as_posix()}: "
                    f"instruments={universe!r} effective benchmark={effective!r} "
                    f"(canonical for that universe: {expected!r})"
                )
        self.assertEqual(
            [], offenders,
            "tracked config(s) pair a universe with the WRONG canonical benchmark "
            "— excess return measured against another universe's basket is a "
            "category error, not a comparison: " + "; ".join(offenders),
        )

    def test_regen_a_price_control_preserved(self) -> None:
        from scripts.regen.replay_frozen_baseline import BENCHMARK
        self.assertEqual(
            REGEN_A_PRICE, BENCHMARK,
            "the REGEN-A price-replay generator constant must stay the SH000300 price "
            "index (it is the preserved price control, not the canonical baseline).",
        )
        if not REGEN_A_CONTROL_FIXTURE.is_file():
            self.fail(
                f"REGEN-A control baseline missing at {REGEN_A_CONTROL_FIXTURE} — PR-2 must "
                "PRESERVE REGEN-A as the price control, not delete it.",
            )
        ctrl = json.loads(REGEN_A_CONTROL_FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(
            REGEN_A_PRICE, str(ctrl.get("_provenance", {}).get("benchmark_code", "")),
            "the preserved REGEN-A control baseline must measure excess vs the SH000300 "
            "price index.",
        )

    def test_tr_price_pairing_intact(self) -> None:
        # The runtime derives the TR<->price pair by the "+TR" suffix convention
        # (BacktestRunner: price_code = tr_code[:-2]). The canonical TR's derived price
        # sibling MUST be exactly the preserved control, so the cumulative-return
        # cross-check pairs SH000300TR with SH000300.
        self.assertTrue(
            CANONICAL_TR.endswith("TR"),
            f"canonical benchmark {CANONICAL_TR!r} must be a total-return ('TR'-suffixed) code.",
        )
        self.assertEqual(
            REGEN_A_PRICE, CANONICAL_TR[:-2],
            f"the canonical TR {CANONICAL_TR!r} suffix-strips to {CANONICAL_TR[:-2]!r}, which "
            f"must equal the price control {REGEN_A_PRICE!r}; otherwise the runtime "
            "TR<->price cumulative-return cross-check pairs the wrong sibling.",
        )


if __name__ == "__main__":
    unittest.main()
