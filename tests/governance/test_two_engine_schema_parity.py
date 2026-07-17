"""Governance: "Two engines, one schema" as a deterministic tripwire
(hardening backlog #2).

``AGENTS.md`` requires Pipeline and WalkForwardEngine to keep field names
identical across their parallel artifacts (``pipeline_report.json`` /
``walk_forward_report.json`` / ``output/runs/_index.jsonl``) — add or rename a
key in one, change the other in the same commit. Until now only two spot
checks existed (git provenance, #313; per-fold timing, #163); an asymmetric
key added anywhere else sailed through review after review.

The two engines legitimately DIFFER in shape (a single-fold run has no
``num_folds``; an aggregate has no single ``dataset`` block), so the honest
deterministic form is NOT whole-file equality — it is a PINNED
shared-core + explicit per-engine extras decomposition:

    engine_keys == SHARED | ENGINE_SPECIFIC        (for each engine)

Adding a key to one engine breaks its pin and the failure message forces the
choice into the open: mirror it in the other engine (extend SHARED) or record
it as engine-specific WITH a justification here. Silent asymmetry is the one
thing that can no longer happen.

Sources of truth exercised:

* top-level report keys — REAL artifacts written through each engine's
  writer (``Pipeline._write_report`` with stub results, mirroring
  ReportGitProvenanceTests; ``build_aggregate_report`` with a stub fold);
* ``_index.jsonl`` record — the shared ``build_record`` signature plus the
  ``config_summary`` / ``headline_metrics`` dict literals each engine passes,
  extracted from the engine sources by AST (no runtime change, no full-run
  fixture needed).

The config BLOCK's inner keys are deliberately NOT pinned: pipeline embeds a
curated summary while walk-forward embeds ``asdict(config)`` — different
shapes by design (single window vs rolling spec); parity there is owned by
the fields' consumers (e.g. label_horizon_days is spot-pinned by #318 tests).
"""
from __future__ import annotations

import ast
import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# The registered schema decomposition. Extending SHARED = mirroring the key in
# BOTH engines in the same commit (the AGENTS.md rule). Extending an
# engine-specific set = declaring, here and reviewably, why the concept cannot
# exist in the other engine.
# ---------------------------------------------------------------------------
REPORT_SHARED = {"generated_at", "git_commit", "git_dirty", "config"}
REPORT_PIPELINE_ONLY = {
    # single-run shape: one dataset/model/backtest, sections inline
    "metric_status", "official_backtest_path", "dataset", "model",
    "signal_analysis", "backtest", "risk_analysis", "factor_analysis",
    "attribution",
    # CSI800 guard-2 (v2-csi800-expansion-guards): per-sleeve turnover is
    # MIRRORED in walk-forward, but at FOLD level (positions are
    # fold-scoped in the rolling shape — fold_report.json carries the
    # same key, pinned by test_walk_forward's additive-schema test); the
    # single-run shape has exactly one positions series, so it sits
    # top-level here. Not aggregate-level drift.
    "sleeve_turnover",
}
REPORT_WALK_FORWARD_ONLY = {
    # rolling shape: per-fold rows + cross-fold aggregation
    "folds", "aggregate_metrics", "test_window_coverage", "num_folds",
}

CONFIG_SUMMARY_SHARED = {
    "instruments", "feature_handler", "label_horizon_days",
    "delisted_registry_path", "model_type", "topk",
}
CONFIG_SUMMARY_PIPELINE_ONLY: set[str] = set()
CONFIG_SUMMARY_WALK_FORWARD_ONLY = {
    # the rolling spec: no single train/test window to summarize
    "ensemble_window", "overall_start", "overall_end",
}

HEADLINE_SHARED = {"mean_ic_1d", "annualized_return"}
HEADLINE_PIPELINE_ONLY: set[str] = set()
HEADLINE_WALK_FORWARD_ONLY = {
    # cross-fold statistics a single run cannot have
    "num_folds", "worst_drawdown", "mean_information_ratio",
}


def _assert_decomposition(
    tc: unittest.TestCase, *, what: str, engine: str,
    actual: set[str], shared: set[str], specific: set[str],
) -> None:
    expected = shared | specific
    tc.assertEqual(
        actual, expected,
        msg=(
            f"{what} keys drifted for engine={engine}.\n"
            f"  missing:    {sorted(expected - actual)}\n"
            f"  unexpected: {sorted(actual - expected)}\n\n"
            "Two engines, one schema (AGENTS.md): a new key must either be "
            "mirrored in the OTHER engine in the same commit (then add it to "
            "the SHARED set here) or be declared engine-specific with a "
            "justification comment in this test."
        ),
    )


# ---------------------------------------------------------------------------
# Top-level report keys — via the REAL writers.
# ---------------------------------------------------------------------------
class TopLevelReportParityTests(unittest.TestCase):
    @staticmethod
    def _pipeline_report_keys() -> set[str]:
        from types import SimpleNamespace

        from src.core.pipeline import Pipeline
        from src.core.signal_analyzer import SignalAnalysisResult

        config = SimpleNamespace(
            instruments="csi300", feature_handler="alpha158",
            label_horizon_days=1,
            train_start="2022-01-01", train_end="2022-12-31",
            valid_start="2023-01-01", valid_end="2023-03-31",
            test_start="2023-04-01", test_end="2023-06-30",
            model_type="LGBModel", benchmark_code="SH000300",
            topk=50, n_drop=5, industry_taxonomy_id=None,
            delisted_registry_path="",
        )
        feature_result = SimpleNamespace(
            train_shape=(10, 5), valid_shape=(5, 5), test_shape=(5, 5),
        )
        model_result = SimpleNamespace(
            prediction_shape=(5, 1), model_artifact_path="m.pkl",
        )
        signal_result = SignalAnalysisResult(
            ic_summary={1: {"mean_ic": 0.01, "std_ic": 0.02, "ir": 0.5,
                            "num_days": 5}},
            ic_series={}, ic_decay=[0.01], turnover_stats={"mean_turnover": 0.1},
        )
        backtest_output = SimpleNamespace(
            metric_status="ok", official_backtest_path="official",
            report={}, provenance={}, risk_analysis={},
        )
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "pipeline_report.json"
            Pipeline._write_report(
                str(path), config, feature_result, model_result,
                signal_result, backtest_output,
                factor_skipped_reason="unit-test",
                git_provenance={"commit": "cafebabe" * 5, "dirty": False},
            )
            return set(json.loads(path.read_text(encoding="utf-8")))

    @staticmethod
    def _walk_forward_report_keys() -> set[str]:
        from src.core.walk_forward import WalkForwardConfig
        from src.core.walk_forward._types import WalkForwardFold
        from src.core.walk_forward.aggregate import build_aggregate_report

        fold = WalkForwardFold(
            fold_index=0,
            train_period="2022-01-01 ~ 2022-12-31",
            valid_period="2023-01-01 ~ 2023-03-31",
            test_period="2023-04-01 ~ 2023-06-30",
            ic_1d=0.01, ic_5d=0.02, annualized_return=0.05,
            max_drawdown=-0.1, information_ratio=0.5,
            prediction_shape=(5, 1), report_path="fold_00_report.json",
        )
        report = build_aggregate_report(
            config=WalkForwardConfig(),
            folds=[fold],
            aggregate_metrics={"mean_ic_1d": 0.01},
            git_provenance={"commit": "cafebabe" * 5, "dirty": False},
        )
        return set(report)

    def test_pipeline_report_top_level_keys(self) -> None:
        _assert_decomposition(
            self, what="pipeline_report.json top-level", engine="pipeline",
            actual=self._pipeline_report_keys(),
            shared=REPORT_SHARED, specific=REPORT_PIPELINE_ONLY,
        )

    def test_walk_forward_report_top_level_keys(self) -> None:
        _assert_decomposition(
            self, what="walk_forward_report.json top-level",
            engine="walk_forward",
            actual=self._walk_forward_report_keys(),
            shared=REPORT_SHARED, specific=REPORT_WALK_FORWARD_ONLY,
        )

    def test_shared_and_specific_sets_are_disjoint(self) -> None:
        # a key in SHARED and a *_ONLY set at once would make the pins
        # tautological — the decomposition must be a real partition.
        self.assertFalse(REPORT_SHARED & (REPORT_PIPELINE_ONLY
                                          | REPORT_WALK_FORWARD_ONLY))
        self.assertFalse(CONFIG_SUMMARY_SHARED & (
            CONFIG_SUMMARY_PIPELINE_ONLY | CONFIG_SUMMARY_WALK_FORWARD_ONLY))
        self.assertFalse(HEADLINE_SHARED & (
            HEADLINE_PIPELINE_ONLY | HEADLINE_WALK_FORWARD_ONLY))


# ---------------------------------------------------------------------------
# _index.jsonl record — shared builder + AST-extracted per-engine dict keys.
# ---------------------------------------------------------------------------
def _call_dict_kwarg_keys(source_path: Path, *, callee: str,
                          kwarg: str) -> set[str]:
    """String keys of the dict literal passed as ``kwarg=...`` to the
    ``callee(...)`` call in ``source_path``. Fails loud (not empty-set) when
    the call or literal is not found — a refactor that moves the record
    construction must move this extraction with it, not silently pass."""
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else (
            node.func.attr if isinstance(node.func, ast.Attribute) else None)
        if name != callee:
            continue
        for kw in node.keywords:
            if kw.arg == kwarg:
                if not isinstance(kw.value, ast.Dict):
                    raise AssertionError(
                        f"{source_path.name}: {callee}({kwarg}=...) is not a "
                        "dict LITERAL — the schema pin cannot read it; keep "
                        "the record dicts literal (or rewrite this test)."
                    )
                keys = set()
                for k in kw.value.keys:
                    if not isinstance(k, ast.Constant) or not isinstance(k.value, str):
                        raise AssertionError(
                            f"{source_path.name}: non-literal key in "
                            f"{callee}({kwarg}=...) — schema pin cannot read it."
                        )
                    keys.add(k.value)
                return keys
    raise AssertionError(
        f"{source_path.name}: no {callee}(..., {kwarg}=...) call found — "
        "the run-catalog record construction moved; update this test's "
        "extraction to follow it."
    )


class RunCatalogRecordParityTests(unittest.TestCase):
    _PIPELINE_SRC = PROJECT_ROOT / "src" / "core" / "pipeline.py"
    _WF_SRC = PROJECT_ROOT / "src" / "core" / "walk_forward" / "engine.py"

    def test_top_level_record_schema_is_single_sourced(self) -> None:
        # Both engines build the record through run_catalog.build_record —
        # two records built with each engine tag must carry identical keys.
        from src.core.run_catalog import build_record

        keys_p = set(build_record(engine="pipeline", status="ok"))
        keys_w = set(build_record(engine="walk_forward", status="ok"))
        self.assertEqual(keys_p, keys_w)

    def test_config_summary_keys(self) -> None:
        _assert_decomposition(
            self, what="_index.jsonl config_summary", engine="pipeline",
            actual=_call_dict_kwarg_keys(
                self._PIPELINE_SRC, callee="build_catalog_record",
                kwarg="config_summary"),
            shared=CONFIG_SUMMARY_SHARED, specific=CONFIG_SUMMARY_PIPELINE_ONLY,
        )
        _assert_decomposition(
            self, what="_index.jsonl config_summary", engine="walk_forward",
            actual=_call_dict_kwarg_keys(
                self._WF_SRC, callee="build_record", kwarg="config_summary"),
            shared=CONFIG_SUMMARY_SHARED,
            specific=CONFIG_SUMMARY_WALK_FORWARD_ONLY,
        )

    def test_headline_metrics_keys(self) -> None:
        _assert_decomposition(
            self, what="_index.jsonl headline_metrics", engine="pipeline",
            actual=_call_dict_kwarg_keys(
                self._PIPELINE_SRC, callee="build_catalog_record",
                kwarg="headline_metrics"),
            shared=HEADLINE_SHARED, specific=HEADLINE_PIPELINE_ONLY,
        )
        _assert_decomposition(
            self, what="_index.jsonl headline_metrics", engine="walk_forward",
            actual=_call_dict_kwarg_keys(
                self._WF_SRC, callee="build_record", kwarg="headline_metrics"),
            shared=HEADLINE_SHARED, specific=HEADLINE_WALK_FORWARD_ONLY,
        )


class ExtractorSelfTests(unittest.TestCase):
    """The AST extractor must fail LOUD, not pass empty, when the record
    construction moves — otherwise the parity pins above rot silently."""

    def _tmp(self, text: str) -> Path:
        d = tempfile.mkdtemp()
        p = Path(d) / "mod.py"
        p.write_text(text, encoding="utf-8")
        return p

    def test_extracts_literal_keys(self) -> None:
        p = self._tmp("build_record(engine='x', config_summary={'a': 1, 'b': 2})\n")
        self.assertEqual(
            _call_dict_kwarg_keys(p, callee="build_record",
                                  kwarg="config_summary"),
            {"a", "b"},
        )

    def test_missing_call_fails_loud(self) -> None:
        p = self._tmp("x = 1\n")
        with self.assertRaises(AssertionError):
            _call_dict_kwarg_keys(p, callee="build_record",
                                  kwarg="config_summary")

    def test_non_literal_dict_fails_loud(self) -> None:
        p = self._tmp("build_record(config_summary=make_summary())\n")
        with self.assertRaises(AssertionError):
            _call_dict_kwarg_keys(p, callee="build_record",
                                  kwarg="config_summary")

    def test_attribute_callee_matches(self) -> None:
        p = self._tmp("mod.build_record(config_summary={'a': 1})\n")
        self.assertEqual(
            _call_dict_kwarg_keys(p, callee="build_record",
                                  kwarg="config_summary"),
            {"a"},
        )


if __name__ == "__main__":
    unittest.main()
