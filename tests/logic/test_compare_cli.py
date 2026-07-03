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
               generated: str = "2025-07-01T00:00:00Z",
               git_commit: str | None = None,
               git_dirty: bool | None = False) -> Path:
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
        "git_commit": git_commit, "git_dirty": git_dirty,
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

    def test_indistinguishable_verdict_carries_not_equivalent_note(self) -> None:
        # codex #312 P2: an indistinguishable verdict MUST surface the mandated
        # "not 'equivalent'" note in CLI output (the n_drop 'pick either' trap guard).
        rng = np.random.default_rng(2)
        d = _dates(250)
        base = rng.standard_normal(250) * 0.01
        treat = base + rng.standard_normal(250) * 0.01  # noisy, zero-mean diff
        with TemporaryDirectory() as tmp:
            a = _write_run(Path(tmp) / "A", d, base)
            b = _write_run(Path(tmp) / "B", d, treat)
            out = "\n".join(self.cli.build_ruler_report(a, b, prereg=_PREREG))
        self.assertIn("INDISTINGUISHABLE", out)
        self.assertIn("not 'equivalent'", out.lower().replace('"', "'"))

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

    def test_record_only_prereg_is_loudly_marked_unverified(self) -> None:
        rng = np.random.default_rng(3)
        d = _dates(250)
        base = rng.standard_normal(250) * 0.01
        treat = base + 0.002 + rng.standard_normal(250) * 0.001
        with TemporaryDirectory() as tmp:
            a = _write_run(Path(tmp) / "A", d, base)
            b = _write_run(Path(tmp) / "B", d, treat)
            out = "\n".join(self.cli.build_ruler_report(a, b, prereg=_PREREG))
        self.assertIn("RECORD-ONLY", out)
        self.assertIn("NOT git-verified", out)

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


_PLAN_YAML = """\
hypothesis: "5d label decays slower than 2d"
expected_direction: treatment_better
baseline: canonical-2d
treatments: ["5d", "10d"]
"""


def _git(repo: Path, *args: str) -> str:
    import subprocess
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, timeout=15, check=True,
    )
    return completed.stdout.strip()


def _repo_with_plan(root: Path) -> tuple[Path, Path, str]:
    """git repo with a committed plan, then one more commit (the 'run code')."""
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    _git(root, "config", "commit.gpgsign", "false")
    plan = root / "plan.yaml"
    plan.write_text(_PLAN_YAML, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "register plan", "--no-verify")
    (root / "code.py").write_text("x = 1\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "run code", "--no-verify")
    return root, plan, _git(root, "rev-parse", "HEAD")


class PreregGateCliTests(unittest.TestCase):
    """--prereg-plan end-to-end: the git-provable gate wired into the renderer."""

    def setUp(self) -> None:
        self.cli = _load_cli()

    def _runs(self, tmp: str, run_commit: str | None) -> tuple[Path, Path]:
        rng = np.random.default_rng(3)
        d = _dates(250)
        base = rng.standard_normal(250) * 0.01
        treat = base + 0.002 + rng.standard_normal(250) * 0.001
        a = _write_run(Path(tmp) / "A", d, base, git_commit=run_commit)
        b = _write_run(Path(tmp) / "B", d, treat, git_commit=run_commit)
        return a, b

    def test_gate_passes_and_verdict_carries_plan_commit(self) -> None:
        with TemporaryDirectory() as tmp:
            repo, plan, run_commit = _repo_with_plan(Path(tmp) / "repo")
            a, b = self._runs(tmp, run_commit)
            out = "\n".join(self.cli.build_ruler_report(
                a, b, prereg_plan=str(plan), variant="5d"))
            plan_commit = _git(repo, "log", "-n", "1", "--format=%H", "--", "plan.yaml")
        self.assertIn("GATE: PASSED", out)
        self.assertIn("VERDICT:", out)
        self.assertIn("hypothesis:", out)
        self.assertNotIn("** FLAG", out)                    # registered variant: no flag
        self.assertIn("direction vs plan: as registered", out)
        # the recorded ref is the PLAN's commit (the registration), not a free string
        self.assertIn(f"pre-registration ref: {plan_commit}", out)

    def test_unregistered_variant_is_flagged(self) -> None:
        with TemporaryDirectory() as tmp:
            _, plan, run_commit = _repo_with_plan(Path(tmp) / "repo")
            a, b = self._runs(tmp, run_commit)
            out = "\n".join(self.cli.build_ruler_report(
                a, b, prereg_plan=str(plan), variant="7d"))
        self.assertIn("GATE: PASSED", out)                  # ancestry itself holds
        self.assertIn("** FLAG", out)
        self.assertIn("UNREGISTERED MULTIPLE COMPARISON", out)

    def test_plan_edited_after_runs_gets_no_verdict(self) -> None:
        with TemporaryDirectory() as tmp:
            repo, plan, run_commit = _repo_with_plan(Path(tmp) / "repo")
            plan.write_text(_PLAN_YAML + "# tweak\n", encoding="utf-8")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-q", "-m", "post-hoc edit", "--no-verify")
            a, b = self._runs(tmp, run_commit)
            out = "\n".join(self.cli.build_ruler_report(
                a, b, prereg_plan=str(plan), variant="5d"))
        self.assertIn("NO VERDICT", out)
        self.assertIn("gate failed", out.lower())
        self.assertNotIn("VERDICT: TREATMENT", out)

    def test_run_without_provenance_gets_no_verdict(self) -> None:
        with TemporaryDirectory() as tmp:
            _, plan, _ = _repo_with_plan(Path(tmp) / "repo")
            a, b = self._runs(tmp, run_commit=None)         # pre-#313 runs
            out = "\n".join(self.cli.build_ruler_report(
                a, b, prereg_plan=str(plan), variant="5d"))
        self.assertIn("NO VERDICT", out)
        self.assertIn("git_commit", out)

    def test_plan_without_variant_gets_actionable_note(self) -> None:
        with TemporaryDirectory() as tmp:
            _, plan, run_commit = _repo_with_plan(Path(tmp) / "repo")
            a, b = self._runs(tmp, run_commit)
            out = "\n".join(self.cli.build_ruler_report(
                a, b, prereg_plan=str(plan), variant=None))
        self.assertIn("--variant", out)
        self.assertNotIn("VERDICT: TREATMENT", out)

    def test_gate_path_on_missing_run_dir_never_raises(self) -> None:
        # build_ruler_report's never-raises contract: a non-run dir inside the gate
        # path (SystemExit from _load_aggregate) renders NO VERDICT, not an exception.
        with TemporaryDirectory() as tmp:
            _, plan, run_commit = _repo_with_plan(Path(tmp) / "repo")
            _, b = self._runs(tmp, run_commit)
            out = "\n".join(self.cli.build_ruler_report(
                Path(tmp) / "NOT_A_RUN", b, prereg_plan=str(plan), variant="5d"))
        self.assertIn("NO VERDICT", out)
        self.assertIn("walk_forward_report.json", out)

    def test_main_wires_prereg_plan_through(self) -> None:
        with TemporaryDirectory() as tmp:
            _, plan, run_commit = _repo_with_plan(Path(tmp) / "repo")
            a, b = self._runs(tmp, run_commit)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = self.cli.main([
                    str(a), str(b),
                    "--prereg-plan", str(plan), "--variant", "5d",
                ])
        text = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("GATE: PASSED", text)
        self.assertIn("VERDICT:", text)


if __name__ == "__main__":
    unittest.main()
