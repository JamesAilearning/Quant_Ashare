"""Audit P2 tail (P0-6 follow-up): the canonical callers thread the run-level
PIT provider into ``BacktestRunner.run``.

The runner-internal wiring has existed since Phase D.3 / audit P0-3
(``run(pit_provider=...)`` → alignment validation → microstructure mask →
equal-weight baseline, each with a WARN fallback) — what was missing is the
three OFFICIAL call sites passing the provider they already hold. Raw-field
fetches carry no window operators, so the §4.3.2 mask is expected to be a
NO-OP on the Phase-B.2 bundle (the PR-2 verdict class); the CI REGEN-2 leg is
the judge, and any drift goes through the re-sign channel.
"""
from __future__ import annotations

import importlib.util
import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _run_call_blocks(source: str) -> list[str]:
    """Every ``BacktestRunner.run(`` call block (to its closing paren line)."""
    blocks: list[str] = []
    for m in re.finditer(r"BacktestRunner\.run\(", source):
        depth, i = 0, m.end() - 1
        while i < len(source):
            if source[i] == "(":
                depth += 1
            elif source[i] == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        blocks.append(source[m.start():i + 1])
    return blocks


class CanonicalCallersThreadProviderTests(unittest.TestCase):
    """Source-level pins (the call sites live inline in heavy methods — same
    style as the st_mask_mode engine wiring pin): every official
    ``BacktestRunner.run`` invocation passes ``pit_provider=pit_provider``."""

    def _assert_all_calls_thread(self, rel: str) -> None:
        source = (PROJECT_ROOT / rel).read_text(encoding="utf-8")
        blocks = _run_call_blocks(source)
        self.assertTrue(blocks, f"{rel}: no BacktestRunner.run call found")
        for block in blocks:
            self.assertIn(
                "pit_provider=pit_provider", block,
                msg=(
                    f"{rel}: a BacktestRunner.run call does not thread the "
                    "run-level PIT provider — the microstructure mask and "
                    "equal-weight baseline would silently take the WARN "
                    "fallback on an official path (audit P2 tail / P0-6):\n"
                    + block[:400]
                ),
            )

    def test_walk_forward_engine_threads_provider(self) -> None:
        self._assert_all_calls_thread("src/core/walk_forward/engine.py")

    def test_pipeline_threads_provider(self) -> None:
        self._assert_all_calls_thread("src/core/pipeline.py")

    def test_regen2_replay_threads_provider(self) -> None:
        self._assert_all_calls_thread(
            "scripts/regen/replay_frozen_baseline_regen2.py")


class ReplayFoldThreadingTests(unittest.TestCase):
    """REAL threading through the replay fold: the SAME provider instance the
    caller holds reaches BacktestRunner.run (and SignalAnalyzer.analyze)."""

    def test_replay_fold_passes_the_same_instance(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "_regen2_replay_under_test",
            PROJECT_ROOT / "scripts" / "regen"
            / "replay_frozen_baseline_regen2.py",
        )
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        provider = MagicMock(name="the_pit_provider")
        entry = {
            "scores": MagicMock(name="frozen_scores"),
            "test": {"start": "2021-01-01", "end": "2021-03-31"},
            "train": {"start": "2019-01-01", "end": "2020-09-30"},
            "valid": {"start": "2020-10-01", "end": "2020-12-31"},
            "prediction_shape": [100],
        }
        fake_signal = SimpleNamespace(
            ic_summary={1: {"mean_ic": 0.01}, 5: {"mean_ic": 0.02}},
        )
        fake_output = SimpleNamespace(risk_analysis={"x": 1})
        with (
            patch.object(
                mod.SignalAnalyzer, "analyze",
                MagicMock(return_value=fake_signal),
            ) as fake_analyze,
            patch.object(
                mod.BacktestRunner, "run", MagicMock(return_value=fake_output),
            ) as fake_run,
            patch.object(
                mod, "extract_cost_metrics",
                MagicMock(return_value=(0.1, -0.05, 0.5)),
            ),
        ):
            fold = mod._replay_fold(
                0, entry, "D:/data/nc.parquet", pit_provider=provider,
            )
        self.assertIs(fake_run.call_args.kwargs["pit_provider"], provider)
        self.assertIs(fake_analyze.call_args.kwargs["pit_provider"], provider)
        self.assertEqual(fold.fold_index, 0)


if __name__ == "__main__":
    unittest.main()
