"""Tests for ``src.core.preregistration`` — the git-provable pre-registration gate
(add-run-comparison-methodology, PR-3b-ii).

Uses REAL throwaway git repos (``git init`` in a temp dir): ancestry is a git
property, so the honest test exercises git itself — no mocks of merge-base.
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.preregistration import (  # noqa: E402
    PreregistrationError,
    gate_comparison,
    is_ancestor,
    load_plan,
    run_commit_from_report,
)

_PLAN_YAML = """\
hypothesis: "5d label decays slower; treatment keeps more gross alpha at lower turnover"
expected_direction: treatment_better
baseline: canonical-2d
treatments: ["5d", "10d"]
"""


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, timeout=15, check=True,
    )
    return completed.stdout.strip()


def _init_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    _git(root, "config", "commit.gpgsign", "false")
    return root


def _commit_all(repo: Path, msg: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg, "--no-verify")
    return _git(repo, "rev-parse", "HEAD")


def _write_plan(repo: Path, content: str = _PLAN_YAML,
                name: str = "plan.yaml") -> Path:
    p = repo / name
    p.write_text(content, encoding="utf-8")
    return p


def _report(
    commit: str | None,
    dirty: bool | None = False,
    *,
    st_mask_mode: str | None = "required",
    namechange_path: str | None = "D:/data/all_namechanges.parquet",
    with_config: bool = True,
) -> dict[str, object]:
    """A minimal aggregate report. Real reports embed the full config
    (aggregate.py: ``"config": asdict(config)``); the gate derives ST-handling
    parity from it (codex P1 #323), so the fixture carries the ST-relevant
    keys. ``st_mask_mode=None`` mimics a config that predates the field."""
    report: dict[str, object] = {"git_commit": commit, "git_dirty": dirty}
    if with_config:
        cfg: dict[str, object] = {"namechange_path": namechange_path}
        if st_mask_mode is not None:
            cfg["st_mask_mode"] = st_mask_mode
        report["config"] = cfg
    return report


class LoadPlanTests(unittest.TestCase):
    def test_committed_plan_loads_with_its_commit(self) -> None:
        with TemporaryDirectory() as td:
            repo = _init_repo(Path(td) / "r")
            plan_path = _write_plan(repo)
            plan_commit = _commit_all(repo, "register plan")
            plan = load_plan(plan_path)
        self.assertEqual(plan.commit, plan_commit)
        self.assertEqual(plan.expected_direction, "treatment_better")
        self.assertEqual(plan.treatments, ("5d", "10d"))
        self.assertEqual(plan.baseline, "canonical-2d")

    def test_uncommitted_plan_rejected(self) -> None:
        # an uncommitted plan is not a registration — it can still be edited post-hoc
        with TemporaryDirectory() as td:
            repo = _init_repo(Path(td) / "r")
            (repo / "seed.txt").write_text("x")
            _commit_all(repo, "seed")
            plan_path = _write_plan(repo)  # written but NOT committed
            with self.assertRaises(PreregistrationError) as cm:
                load_plan(plan_path)
        self.assertIn("UNCOMMITTED", str(cm.exception))

    def test_plan_with_local_edits_rejected(self) -> None:
        # committed, then locally edited: the committed content is the registration,
        # and a divergent working copy must refuse rather than gate on stale content.
        with TemporaryDirectory() as td:
            repo = _init_repo(Path(td) / "r")
            plan_path = _write_plan(repo)
            _commit_all(repo, "register plan")
            plan_path.write_text(_PLAN_YAML + "# post-hoc tweak\n", encoding="utf-8")
            with self.assertRaises(PreregistrationError):
                load_plan(plan_path)

    def test_missing_or_malformed_fields_rejected(self) -> None:
        cases = [
            "hypothesis: ''\nexpected_direction: treatment_better\nbaseline: b\ntreatments: [x]\n",
            "hypothesis: h\nexpected_direction: sideways\nbaseline: b\ntreatments: [x]\n",
            "hypothesis: h\nexpected_direction: treatment_better\nbaseline: ''\ntreatments: [x]\n",
            "hypothesis: h\nexpected_direction: treatment_better\nbaseline: b\ntreatments: []\n",
            "- not\n- a\n- mapping\n",
        ]
        with TemporaryDirectory() as td:
            repo = _init_repo(Path(td) / "r")
            for k, content in enumerate(cases):
                plan_path = _write_plan(repo, content, name=f"plan_{k}.yaml")
                _commit_all(repo, f"plan {k}")
                with self.assertRaises(PreregistrationError, msg=f"case {k}"):
                    load_plan(plan_path)

    def test_missing_file_rejected(self) -> None:
        with self.assertRaises(PreregistrationError):
            load_plan(Path("Z:/nonexistent/plan.yaml"))


class RunCommitTests(unittest.TestCase):
    def test_clean_commit_accepted(self) -> None:
        self.assertEqual(
            run_commit_from_report(_report("abc123", dirty=False), run_label="baseline run"),
            "abc123",
        )

    def test_missing_commit_rejected_actionably(self) -> None:
        with self.assertRaises(PreregistrationError) as cm:
            run_commit_from_report(_report(None), run_label="baseline run")
        self.assertIn("re-run", str(cm.exception).lower())

    def test_dirty_or_unknown_worktree_rejected(self) -> None:
        for dirty in (True, None):
            with self.assertRaises(PreregistrationError):
                run_commit_from_report(_report("abc123", dirty=dirty), run_label="t")


def _folds(
    sha: str | None = "cafebabe12345678", n: int = 2,
) -> list[dict[str, object]]:
    """Per-fold reports with st_mask provenance (the content-hash evidence
    the gate requires for ST-on runs; codex P1 #323 r3) — in the REAL
    ``write_fold_report`` shape (``backtest.provenance.st_mask``; codex P1
    r4 caught a flat-shape draft). The writer-reader consistency test below
    pins this shape against ``build_fold_report`` itself."""
    st: dict[str, object] = {"namechange_path": "D:/data/all_namechanges.parquet"}
    if sha is not None:
        st["namechange_sha256"] = sha
    return [
        {"backtest": {"provenance": {"st_mask": dict(st)}}} for _ in range(n)
    ]


class GateTests(unittest.TestCase):
    def _repo_with_plan_then_run_commit(self, td: str) -> tuple[Path, object, str]:
        repo = _init_repo(Path(td) / "r")
        plan_path = _write_plan(repo)
        _commit_all(repo, "register plan")
        (repo / "code.py").write_text("x = 1\n")
        run_commit = _commit_all(repo, "the code that produced the runs")
        return repo, load_plan(plan_path), run_commit

    def test_plan_ancestor_of_runs_passes(self) -> None:
        with TemporaryDirectory() as td:
            _, plan, run_commit = self._repo_with_plan_then_run_commit(td)
            flags = gate_comparison(
                plan,
                baseline_report=_report(run_commit),
                treatment_report=_report(run_commit),
                variant="5d",
                baseline_fold_reports=_folds(),
                treatment_fold_reports=_folds(),
            )
        self.assertEqual(flags, [])

    def test_plan_same_commit_as_run_passes(self) -> None:
        # plan committed, run launched from that same commit: the plan precedes the
        # run's execution — a commit is its own ancestor.
        with TemporaryDirectory() as td:
            repo = _init_repo(Path(td) / "r")
            plan_path = _write_plan(repo)
            c = _commit_all(repo, "plan + code together")
            plan = load_plan(plan_path)
            flags = gate_comparison(
                plan, baseline_report=_report(c), treatment_report=_report(c),
                variant="5d",
                baseline_fold_reports=_folds(),
                treatment_fold_reports=_folds(),
            )
        self.assertEqual(flags, [])

    def test_plan_edited_after_runs_rejected(self) -> None:
        # THE core property: post-hoc editing moves the plan's last-touched commit
        # PAST the run commits -> not an ancestor -> the gate refuses.
        with TemporaryDirectory() as td:
            repo, _, run_commit = self._repo_with_plan_then_run_commit(td)
            (repo / "plan.yaml").write_text(
                _PLAN_YAML.replace("treatment_better", "treatment_worse"),
                encoding="utf-8",
            )
            _commit_all(repo, "post-hoc plan edit")
            edited_plan = load_plan(repo / "plan.yaml")
            with self.assertRaises(PreregistrationError) as cm:
                gate_comparison(
                    edited_plan,
                    baseline_report=_report(run_commit),
                    treatment_report=_report(run_commit),
                    variant="5d",
                )
        self.assertIn("post-hoc", str(cm.exception))

    def test_run_from_unrelated_history_rejected(self) -> None:
        # a run commit the plan does not precede (sibling branch from before the plan)
        with TemporaryDirectory() as td:
            repo = _init_repo(Path(td) / "r")
            (repo / "seed.txt").write_text("x")
            seed = _commit_all(repo, "seed")
            plan_path = _write_plan(repo)
            _commit_all(repo, "register plan")
            plan = load_plan(plan_path)
            _git(repo, "checkout", "-q", "-b", "sibling", seed)
            (repo / "other.py").write_text("y = 2\n")
            sibling_commit = _commit_all(repo, "sibling run code (no plan in history)")
            _git(repo, "checkout", "-q", "-")  # restore so the plan file exists
            with self.assertRaises(PreregistrationError):
                gate_comparison(
                    plan,
                    baseline_report=_report(sibling_commit),
                    treatment_report=_report(sibling_commit),
                    variant="5d",
                )

    def test_unregistered_variant_flagged_not_refused(self) -> None:
        with TemporaryDirectory() as td:
            _, plan, run_commit = self._repo_with_plan_then_run_commit(td)
            flags = gate_comparison(
                plan,
                baseline_report=_report(run_commit),
                treatment_report=_report(run_commit),
                variant="7d",  # NOT in the registered {5d, 10d}
                baseline_fold_reports=_folds(),
                treatment_fold_reports=_folds(),
            )
        self.assertEqual(len(flags), 1)
        self.assertIn("UNREGISTERED MULTIPLE COMPARISON", flags[0])

    def test_st_handling_mismatch_refused(self) -> None:
        # codex P1 #323: one side ST-on, one ST-off — the pair measures the
        # ST interaction, not the registered hypothesis. HARD refusal.
        with TemporaryDirectory() as td:
            _, plan, run_commit = self._repo_with_plan_then_run_commit(td)
            with self.assertRaises(PreregistrationError) as cm:
                gate_comparison(
                    plan,
                    baseline_report=_report(run_commit),  # required + inputs
                    treatment_report=_report(
                        run_commit, st_mask_mode="off_experiment",
                        namechange_path="",
                    ),
                    variant="5d",
                )
        self.assertIn("ST-handling MISMATCH", str(cm.exception))

    def test_st_off_both_sides_passes(self) -> None:
        # the 阶段6 campaign shape: off_experiment + no ST inputs on BOTH sides.
        with TemporaryDirectory() as td:
            _, plan, run_commit = self._repo_with_plan_then_run_commit(td)
            flags = gate_comparison(
                plan,
                baseline_report=_report(
                    run_commit, st_mask_mode="off_experiment", namechange_path="",
                ),
                treatment_report=_report(
                    run_commit, st_mask_mode="off_experiment", namechange_path="",
                ),
                variant="5d",
            )
        self.assertEqual(flags, [])

    def test_st_on_with_different_namechange_inputs_refused(self) -> None:
        # codex P1 #323 round 2: presence is not parity — two ST-on runs fed
        # DIFFERENT namechange snapshots exclude different ST sets; the pair
        # measures an input change, not the registered variant.
        with TemporaryDirectory() as td:
            _, plan, run_commit = self._repo_with_plan_then_run_commit(td)
            with self.assertRaises(PreregistrationError) as cm:
                gate_comparison(
                    plan,
                    baseline_report=_report(
                        run_commit, namechange_path="D:/data/nc_20260101.parquet",
                    ),
                    treatment_report=_report(
                        run_commit, namechange_path="D:/data/nc_20260601.parquet",
                    ),
                    variant="5d",
                )
        self.assertIn("ST-handling MISMATCH", str(cm.exception))

    def test_cosmetic_path_spelling_is_not_a_mismatch(self) -> None:
        # separator/case-normalized comparison: the SAME file spelled with
        # different separators (or case, on Windows) must not false-refuse.
        with TemporaryDirectory() as td:
            _, plan, run_commit = self._repo_with_plan_then_run_commit(td)
            flags = gate_comparison(
                plan,
                baseline_report=_report(
                    run_commit, namechange_path="D:/data/all_namechanges.parquet",
                ),
                treatment_report=_report(
                    run_commit, namechange_path="D:\\data\\all_namechanges.parquet",
                ),
                variant="5d",
                baseline_fold_reports=_folds(),
                treatment_fold_reports=_folds(),
            )
        self.assertEqual(flags, [])

    def test_st_on_same_path_different_content_refused(self) -> None:
        # codex P1 #323 r3: same path, snapshot refreshed IN PLACE between
        # the runs — only the recorded content hash can catch it.
        with TemporaryDirectory() as td:
            _, plan, run_commit = self._repo_with_plan_then_run_commit(td)
            with self.assertRaises(PreregistrationError) as cm:
                gate_comparison(
                    plan,
                    baseline_report=_report(run_commit),
                    treatment_report=_report(run_commit),
                    variant="5d",
                    baseline_fold_reports=_folds("aaaa000011112222"),
                    treatment_fold_reports=_folds("bbbb000011112222"),
                )
        self.assertIn("ST INPUT CONTENT MISMATCH", str(cm.exception))

    def test_st_on_without_fold_hashes_refused(self) -> None:
        # path-only proof is no proof: an ST-on run whose fold reports carry
        # no content hash (or are absent) cannot receive a decision-grade
        # verdict.
        with TemporaryDirectory() as td:
            _, plan, run_commit = self._repo_with_plan_then_run_commit(td)
            with self.assertRaises(PreregistrationError):
                gate_comparison(
                    plan,
                    baseline_report=_report(run_commit),
                    treatment_report=_report(run_commit),
                    variant="5d",
                )
            with self.assertRaises(PreregistrationError) as cm:
                gate_comparison(
                    plan,
                    baseline_report=_report(run_commit),
                    treatment_report=_report(run_commit),
                    variant="5d",
                    baseline_fold_reports=_folds(sha=None),  # provenance, no hash
                    treatment_fold_reports=_folds(),
                )
        self.assertIn("no st_mask content hash", str(cm.exception))

    def test_mid_run_snapshot_refresh_refused(self) -> None:
        # two distinct hashes WITHIN one run = the input moved mid-run.
        with TemporaryDirectory() as td:
            _, plan, run_commit = self._repo_with_plan_then_run_commit(td)
            with self.assertRaises(PreregistrationError) as cm:
                gate_comparison(
                    plan,
                    baseline_report=_report(run_commit),
                    treatment_report=_report(run_commit),
                    variant="5d",
                    baseline_fold_reports=_folds("aaaa000011112222", n=1)
                    + _folds("bbbb000011112222", n=1),
                    treatment_fold_reports=_folds("aaaa000011112222"),
                )
        self.assertIn("MULTIPLE distinct", str(cm.exception))

    def test_gate_reads_the_shape_build_fold_report_writes(self) -> None:
        # codex P1 #323 r4: the gate must read the SAME nesting the fold
        # report writer produces (backtest.provenance.st_mask) — a
        # hand-guessed fixture shape let a top-level-'provenance' reader
        # pass every unit test while refusing every REAL ST-on run as
        # unproven. Pin reader == writer by feeding an actual
        # build_fold_report product through the hash extractor.
        from unittest.mock import MagicMock

        from src.core.canonical_backtest_contract import CanonicalBacktestOutput
        from src.core.preregistration import _st_input_hashes
        from src.core.signal_analyzer import SignalAnalysisResult
        from src.core.walk_forward.aggregate import build_fold_report

        backtest_output = CanonicalBacktestOutput(
            metric_status="official",
            official_backtest_path="qlib.backtest.backtest",
            return_series={"return": {}, "bench": {}, "cost": {}},
            risk_analysis={"excess_return_with_cost": {
                "annualized_return": 0.11, "max_drawdown": -0.08,
                "information_ratio": 1.2,
            }},
            report={"total_days": 60},
            provenance={"st_mask": {
                "namechange_path": "D:/data/all_namechanges.parquet",
                "namechange_sha256": "feedface00000001",
            }},
            positions={},
        )
        report = build_fold_report(
            fold_index=0,
            train_start="2024-01-01", train_end="2024-06-30",
            valid_start="2024-07-01", valid_end="2024-09-30",
            test_start="2024-10-01", test_end="2024-12-31",
            model_artifact_path="/tmp/model_fold0.pkl",
            model_result=MagicMock(
                best_iteration=3, final_valid_loss=0.95, prediction_shape=(1,),
            ),
            signal_result=SignalAnalysisResult(
                ic_summary={1: {"mean_ic": 0.01}}, ic_series={},
                ic_decay=[0.01], turnover_stats={},
            ),
            backtest_output=backtest_output,
            positions_path=None,
            ic_1d=0.02, ic_5d=0.04, annualized_return=0.11,
            max_drawdown=-0.08, information_ratio=1.2,
        )
        hashes = _st_input_hashes(
            [report], mode="required", run_label="writer-shape run",
        )
        self.assertEqual(hashes, frozenset({"feedface00000001"}))

    def test_st_off_with_recorded_hash_refused(self) -> None:
        # config says off_experiment but fold provenance recorded an ST input
        # hash — the artifacts contradict the config.
        with TemporaryDirectory() as td:
            _, plan, run_commit = self._repo_with_plan_then_run_commit(td)
            off = {"st_mask_mode": "off_experiment", "namechange_path": ""}
            with self.assertRaises(PreregistrationError) as cm:
                gate_comparison(
                    plan,
                    baseline_report=_report(run_commit, **off),  # type: ignore[arg-type]
                    treatment_report=_report(run_commit, **off),  # type: ignore[arg-type]
                    variant="5d",
                    baseline_fold_reports=_folds(),  # hash present despite off
                    treatment_fold_reports=[{}],
                )
        self.assertIn("contradict", str(cm.exception))

    def test_pre_field_config_reads_as_required(self) -> None:
        # a report whose config predates st_mask_mode (key absent) is the old
        # mandatory-mask engine: parity with an explicit "required" run holds.
        with TemporaryDirectory() as td:
            _, plan, run_commit = self._repo_with_plan_then_run_commit(td)
            flags = gate_comparison(
                plan,
                baseline_report=_report(run_commit, st_mask_mode=None),
                treatment_report=_report(run_commit),
                variant="5d",
                baseline_fold_reports=_folds(),
                treatment_fold_reports=_folds(),
            )
        self.assertEqual(flags, [])

    def test_report_without_config_block_refused(self) -> None:
        # no config block -> ST parity unprovable -> no decision-grade verdict.
        with TemporaryDirectory() as td:
            _, plan, run_commit = self._repo_with_plan_then_run_commit(td)
            with self.assertRaises(PreregistrationError) as cm:
                gate_comparison(
                    plan,
                    baseline_report=_report(run_commit, with_config=False),
                    treatment_report=_report(run_commit),
                    variant="5d",
                )
        self.assertIn("no 'config' block", str(cm.exception))

    def test_run_without_provenance_rejected(self) -> None:
        with TemporaryDirectory() as td:
            _, plan, run_commit = self._repo_with_plan_then_run_commit(td)
            with self.assertRaises(PreregistrationError):
                gate_comparison(
                    plan,
                    baseline_report=_report(None),  # pre-#313 run: no git_commit
                    treatment_report=_report(run_commit),
                    variant="5d",
                )

    def test_is_ancestor_unknown_commit_raises_not_false(self) -> None:
        # an unknown sha must raise (actionable), not silently read as "not ancestor"
        with TemporaryDirectory() as td:
            repo = _init_repo(Path(td) / "r")
            (repo / "a.txt").write_text("x")
            c = _commit_all(repo, "one")
            with self.assertRaises(PreregistrationError):
                is_ancestor("0" * 40, c, repo_root=repo)


if __name__ == "__main__":
    unittest.main()
