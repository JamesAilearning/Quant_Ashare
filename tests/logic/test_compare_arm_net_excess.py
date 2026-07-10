"""Tests for scripts/compare_arm_net_excess.py — the one-sample
net-excess-vs-zero evidence generator (codex P1 on #339: the DEAD-END
verdict's no-arm-sig-positive claim must be reproducible from committed
tooling with the ruler's own guards)."""
from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _load_cli():
    path = PROJECT_ROOT / "scripts" / "compare_arm_net_excess.py"
    spec = importlib.util.spec_from_file_location("_arm_net_excess_under_test", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_run(root: Path, excess_by_day: dict[str, float]) -> Path:
    """A minimal run dir carrying the daily_series net-excess substrate the
    ruler reads (mirrors tests/logic/test_compare_cli.py's builder shape)."""
    from src.core.walk_forward.aggregate import FOLD_REPORT_SCHEMA_VERSION

    root.mkdir(parents=True, exist_ok=True)
    days = sorted(excess_by_day)
    ds = {
        "excess_return": dict(excess_by_day),
        "components": {
            "return": {d: excess_by_day[d] + 0.0015 for d in days},
            "bench": {d: 0.001 for d in days},
            "cost": {d: 0.0005 for d in days},
        },
        "ic": {"1": {d: 0.02 for d in days}},
    }
    tp = f"{days[0]}..{days[-1]}"
    (root / "fold_00_report.json").write_text(json.dumps({
        "fold_index": 0, "test_period": tp, "ic_1d": 0.02,
        "annualized_return": 0.05, "information_ratio": 0.3,
        "daily_series": ds, "schema_version": FOLD_REPORT_SCHEMA_VERSION,
    }), encoding="utf-8")
    (root / "walk_forward_report.json").write_text(json.dumps({
        "num_folds": 1, "generated_at": "2026-07-09T00:00:00Z",
        "git_commit": "deadbeef", "git_dirty": False,
        "config": {"st_mask_mode": "off_experiment", "namechange_path": ""},
        "folds": [{"test_period": tp, "fold_index": 0, "ic_1d": 0.02,
                   "annualized_return": 0.05, "information_ratio": 0.3}],
        "aggregate_metrics": {"pooled_ir": 0.3},
    }), encoding="utf-8")
    return root


def _days(n: int, start: str = "2025-01-01") -> list[str]:
    from datetime import date, timedelta

    d0 = date.fromisoformat(start)
    return [(d0 + timedelta(days=i)).isoformat() for i in range(n)]


def _noisy(days: list[str], mean: float, std: float, seed: int) -> dict[str, float]:
    """A realistic (non-periodic) daily series: fixed-seed gaussian noise
    around ``mean`` — avoids the degenerate-CI pathology of a strictly
    periodic synthetic series."""
    import numpy as np

    rng = np.random.default_rng(seed)
    vals = mean + std * rng.standard_normal(len(days))
    return {d: float(v) for d, v in zip(days, vals, strict=True)}


class CompareArmNetExcessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cli = _load_cli()
        self.td = TemporaryDirectory()
        self.root = Path(self.td.name)

    def tearDown(self) -> None:
        self.td.cleanup()

    def _run(self, *dirs: Path) -> tuple[int, str]:
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = self.cli.main([str(d) for d in dirs])
        return rc, buf.getvalue()

    def test_indistinguishable_arm_reported(self) -> None:
        # a near-zero-mean noisy series -> CI straddles zero
        days = _days(200)
        arm = _write_run(self.root / "arm", _noisy(days, 0.0001, 0.002, seed=0))
        rc, out = self._run(arm)
        self.assertEqual(rc, 0)
        self.assertIn("indistinguishable-from-0", out)
        self.assertIn("| arm |", out)

    def test_significantly_positive_arm_reported(self) -> None:
        # a consistently POSITIVE series with REAL spread (0.002..0.003/day)
        # -> a non-degenerate CI that still excludes zero above. (A
        # near-constant positive series would trip the degenerate-CI guard,
        # by design — that is tested implicitly by the guard's existence.)
        days = _days(200)
        # strong positive mean, moderate noise -> CI real-width but > 0
        arm = _write_run(self.root / "pos", _noisy(days, 0.003, 0.001, seed=1))
        rc, out = self._run(arm)
        self.assertEqual(rc, 0)
        self.assertIn("SIG-POSITIVE", out)

    def test_near_constant_positive_trips_degenerate_guard(self) -> None:
        # the guard: a directional (positive) mean on a ~zero-variance series
        # must be REFUSED, never emitted as a real CI.
        days = _days(120)
        arm = _write_run(self.root / "flat", {d: 0.004 for d in days})
        with self.assertRaises(SystemExit):
            self._run(arm)

    def test_too_few_days_fails_loud(self) -> None:
        days = _days(10)  # < DEFAULT_MIN_PAIRED_DAYS
        arm = _write_run(self.root / "short", {d: 0.001 for d in days})
        with self.assertRaises(SystemExit):
            self._run(arm)

    def test_multiple_arms_one_row_each(self) -> None:
        days = _days(200)
        a = _write_run(self.root / "a", _noisy(days, 0.0001, 0.002, seed=2))
        b = _write_run(self.root / "b", _noisy(days, 0.0002, 0.002, seed=3))
        rc, out = self._run(a, b)
        self.assertEqual(rc, 0)
        self.assertIn("| a |", out)
        self.assertIn("| b |", out)


if __name__ == "__main__":
    unittest.main()
