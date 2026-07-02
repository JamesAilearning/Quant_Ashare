"""Tests for ``scripts/compare_walk_forward_runs.py`` — the run-comparison CLI now
emits the trustworthy ruler verdict (PR-2 tail / PR-3a).

Pure synthetic: writes tiny run dirs (aggregate + per-fold reports with the
``daily_series`` substrate) and exercises the ruler-report glue + fail-loud
passthrough without qlib, a bundle, or a real walk-forward.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.walk_forward.aggregate import FOLD_REPORT_SCHEMA_VERSION  # noqa: E402

_PREREG = "abc1234"


def _load_cli() -> Any:
    path = PROJECT_ROOT / "scripts" / "compare_walk_forward_runs.py"
    spec = importlib.util.spec_from_file_location("_compare_cli_under_test", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _dates(n: int, start: str = "2025-07-01") -> list[str]:
    from datetime import date, timedelta
    d0 = date.fromisoformat(start)
    return [(d0 + timedelta(days=i)).isoformat() for i in range(n)]


def _write_run(root: Path, dates: list[str], excess: np.ndarray[Any, Any],
               ic: float = 0.02, schema: str = FOLD_REPORT_SCHEMA_VERSION,
               generated: str = "2025-07-01T00:00:00Z") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    ds = {
        "excess_return": {dates[i]: float(excess[i]) for i in range(len(dates))},
        "components": {
            "return": {dates[i]: float(excess[i]) + 0.0015 for i in range(len(dates))},
            "bench": {d: 0.001 for d in dates},
            "cost": {d: 0.0005 for d in dates},
        },
        "ic": {"1": {d: float(ic) for d in dates}},
    }
    tp = f"{dates[0]}..{dates[-1]}"
    (root / "fold_00_report.json").write_text(json.dumps({
        "fold_index": 0, "test_period": tp, "ic_1d": float(ic),
        "annualized_return": 0.05, "information_ratio": 0.3,
        "daily_series": ds, "schema_version": schema,
    }))
    (root / "walk_forward_report.json").write_text(json.dumps({
        "num_folds": 1, "generated_at": generated,
        "folds": [{"test_period": tp, "fold_index": 0, "ic_1d": float(ic),
                   "annualized_return": 0.05, "information_ratio": 0.3}],
        "aggregate_metrics": {"pooled_ir": 0.3},
    }))
    return root


class CompareCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cli = _load_cli()

    def test_verdict_and_caveats_rendered_on_good_runs(self) -> None:
        rng = np.random.default_rng(3)
        d = _dates(250)
        base = rng.standard_normal(250) * 0.01
        treat = base + 0.002 + rng.standard_normal(250) * 0.001  # clearly better, real width
        with TemporaryDirectory() as tmp:
            a = _write_run(Path(tmp) / "A", d, base)
            b = _write_run(Path(tmp) / "B", d, treat)
            out = "\n".join(self.cli.build_ruler_report(a, b, prereg=_PREREG))
        self.assertIn("VERDICT:", out)
        self.assertIn("treatment_better".upper(), out)   # the clearly-better fixture
        self.assertIn(f"pre-registration ref: {_PREREG}", out)
        self.assertIn("block_length=", out)
        self.assertIn("study-protocol", out.lower())     # honesty envelope present

    def test_missing_prereg_skips_with_actionable_note(self) -> None:
        d = _dates(60)
        with TemporaryDirectory() as tmp:
            a = _write_run(Path(tmp) / "A", d, np.zeros(60))
            b = _write_run(Path(tmp) / "B", d, np.zeros(60))
            out = "\n".join(self.cli.build_ruler_report(a, b, prereg=None))
        self.assertIn("--prereg", out)
        self.assertNotIn("VERDICT:", out)  # no verdict without a pre-registration ref

    def test_non_comparable_substrate_fails_loud_not_crash(self) -> None:
        # an old run without the daily_series substrate -> actionable NO VERDICT, not a crash
        d = _dates(60)
        with TemporaryDirectory() as tmp:
            a = _write_run(Path(tmp) / "A", d, np.zeros(60), schema="1-legacy")
            b = _write_run(Path(tmp) / "B", d, np.zeros(60), schema="1-legacy")
            out = "\n".join(self.cli.build_ruler_report(a, b, prereg=_PREREG))
        self.assertIn("NO VERDICT", out)
        self.assertIn("non-comparable", out.lower())

    def test_main_prints_table_and_verdict(self) -> None:
        rng = np.random.default_rng(7)
        d = _dates(250)
        base = rng.standard_normal(250) * 0.01
        treat = base + 0.002 + rng.standard_normal(250) * 0.001
        with TemporaryDirectory() as tmp:
            a = _write_run(Path(tmp) / "A", d, base)
            b = _write_run(Path(tmp) / "B", d, treat)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = self.cli.main([str(a), str(b), "--prereg", _PREREG])
        text = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("BASELINE :", text)          # the per-fold table header block
        self.assertIn("AGGREGATE METRICS", text)
        self.assertIn("VERDICT:", text)            # the ruler section wired into main


if __name__ == "__main__":
    unittest.main()
