"""Tests for scripts/compare_factor_handlers.py."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.compare_factor_handlers import (  # noqa: E402
    CompareError,
    compare,
)
from scripts.compare_factor_handlers import main as compare_main  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures — synthetic walk-forward reports
# ---------------------------------------------------------------------------


def _write_report(
    path: Path,
    *,
    handler: str,
    mean_information_ratio: float = 0.30,
    mean_ic_1d: float = 0.01,
    mean_annualized_return: float = 0.10,
    worst_drawdown: float = -0.20,
    extra_metrics: dict | None = None,
) -> Path:
    aggregate: dict = {
        "mean_information_ratio": mean_information_ratio,
        "mean_ic_1d": mean_ic_1d,
        "mean_annualized_return": mean_annualized_return,
        "worst_drawdown": worst_drawdown,
    }
    if extra_metrics:
        aggregate.update(extra_metrics)
    report = {
        "generated_at": "2024-01-01T00:00:00Z",
        "config": {"feature_handler": handler},
        "folds": [],
        "aggregate_metrics": aggregate,
        "test_window_coverage": {},
        "num_folds": 8,
    }
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# compare() — basic diff arithmetic
# ---------------------------------------------------------------------------


def test_compare_basic_diff(tmp_path):
    baseline = _write_report(
        tmp_path / "baseline.json", handler="Alpha158",
        mean_information_ratio=0.40, mean_ic_1d=0.02,
    )
    candidate = _write_report(
        tmp_path / "candidate.json", handler="MinedFactor",
        mean_information_ratio=0.50, mean_ic_1d=0.03,
    )
    result = compare(baseline, candidate)
    assert result["baseline_label"] == "Alpha158"
    assert result["candidate_label"] == "MinedFactor"

    ir = result["metrics"]["mean_information_ratio"]
    assert ir["baseline"] == 0.40
    assert ir["candidate"] == 0.50
    assert ir["abs_delta"] == pytest.approx(0.10, abs=1e-9)
    assert ir["rel_delta"] == pytest.approx(0.25, abs=1e-9)


def test_compare_zero_baseline_emits_null_rel_delta(tmp_path):
    baseline = _write_report(
        tmp_path / "baseline.json", handler="Alpha158",
        mean_information_ratio=0.0,
    )
    candidate = _write_report(
        tmp_path / "candidate.json", handler="MinedFactor",
        mean_information_ratio=0.30,
    )
    result = compare(baseline, candidate)
    assert result["metrics"]["mean_information_ratio"]["rel_delta"] is None


# ---------------------------------------------------------------------------
# design_doc_ir_threshold_met flag
# ---------------------------------------------------------------------------


def test_compare_ir_threshold_met_true(tmp_path):
    """Candidate IR >= 1.10 * baseline IR → flag true."""
    baseline = _write_report(
        tmp_path / "baseline.json", handler="Alpha158",
        mean_information_ratio=0.40,
    )
    candidate = _write_report(
        tmp_path / "candidate.json", handler="MinedFactor",
        mean_information_ratio=0.45,  # 0.45 >= 0.44 → met
    )
    result = compare(baseline, candidate)
    assert result["summary"]["design_doc_ir_threshold_met"] is True


def test_compare_ir_threshold_met_false(tmp_path):
    baseline = _write_report(
        tmp_path / "baseline.json", handler="Alpha158",
        mean_information_ratio=0.40,
    )
    candidate = _write_report(
        tmp_path / "candidate.json", handler="MinedFactor",
        mean_information_ratio=0.42,  # 0.42 < 0.44 → not met
    )
    result = compare(baseline, candidate)
    assert result["summary"]["design_doc_ir_threshold_met"] is False


def test_compare_ir_threshold_with_zero_baseline(tmp_path):
    """Zero baseline IR — any non-negative candidate clears the bar."""
    baseline = _write_report(
        tmp_path / "baseline.json", handler="Alpha158",
        mean_information_ratio=0.0,
    )
    candidate = _write_report(
        tmp_path / "candidate.json", handler="MinedFactor",
        mean_information_ratio=0.05,
    )
    result = compare(baseline, candidate)
    assert result["summary"]["design_doc_ir_threshold_met"] is True


def test_compare_ir_threshold_missing_metric_is_none(tmp_path):
    """If IR is missing from either side, threshold flag is None."""
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(
        json.dumps({
            "generated_at": "t",
            "config": {"feature_handler": "Alpha158"},
            "folds": [],
            "aggregate_metrics": {"mean_ic_1d": 0.01},  # no IR
            "test_window_coverage": {},
            "num_folds": 1,
        }),
        encoding="utf-8",
    )
    candidate = _write_report(
        tmp_path / "candidate.json", handler="MinedFactor",
        mean_information_ratio=0.40,
    )
    result = compare(baseline_path, candidate)
    assert result["summary"]["design_doc_ir_threshold_met"] is None


# ---------------------------------------------------------------------------
# Label inference
# ---------------------------------------------------------------------------


def test_compare_labels_inferred_from_config_handler(tmp_path):
    baseline = _write_report(tmp_path / "baseline.json", handler="Alpha158")
    candidate = _write_report(tmp_path / "candidate.json", handler="MinedFactor")
    result = compare(baseline, candidate)
    assert result["baseline_label"] == "Alpha158"
    assert result["candidate_label"] == "MinedFactor"


def test_compare_explicit_labels_override(tmp_path):
    baseline = _write_report(tmp_path / "baseline.json", handler="Alpha158")
    candidate = _write_report(tmp_path / "candidate.json", handler="MinedFactor")
    result = compare(
        baseline, candidate,
        baseline_label="Alpha158-v2", candidate_label="MinedFactor-prod-v3",
    )
    assert result["baseline_label"] == "Alpha158-v2"
    assert result["candidate_label"] == "MinedFactor-prod-v3"


# ---------------------------------------------------------------------------
# Unavailable metrics
# ---------------------------------------------------------------------------


def test_compare_missing_metric_listed_in_unavailable(tmp_path):
    baseline = _write_report(
        tmp_path / "baseline.json", handler="Alpha158",
        extra_metrics={"custom_metric": 0.5},
    )
    candidate = _write_report(tmp_path / "candidate.json", handler="MinedFactor")
    # custom_metric only exists in baseline → missing_in_candidate
    result = compare(baseline, candidate, metrics=("custom_metric", "mean_information_ratio"))
    assert "custom_metric" not in result["metrics"]
    assert result["unavailable_metrics"]["custom_metric"] == "missing_in_candidate"
    assert "mean_information_ratio" in result["metrics"]


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_compare_missing_file_raises(tmp_path):
    baseline = _write_report(tmp_path / "baseline.json", handler="Alpha158")
    with pytest.raises(CompareError, match="does not exist"):
        compare(baseline, tmp_path / "does_not_exist.json")


def test_compare_invalid_json_raises(tmp_path):
    baseline = _write_report(tmp_path / "baseline.json", handler="Alpha158")
    bad = tmp_path / "bad.json"
    bad.write_text("not json {{{", encoding="utf-8")
    with pytest.raises(CompareError, match="not valid JSON"):
        compare(baseline, bad)


def test_compare_report_without_aggregate_metrics_raises(tmp_path):
    baseline = _write_report(tmp_path / "baseline.json", handler="Alpha158")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"generated_at": "t"}), encoding="utf-8")
    with pytest.raises(CompareError, match="aggregate_metrics"):
        compare(baseline, bad)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_writes_output_json(tmp_path):
    baseline = _write_report(tmp_path / "baseline.json", handler="Alpha158")
    candidate = _write_report(tmp_path / "candidate.json", handler="MinedFactor")
    out = tmp_path / "out" / "compare.json"
    rc = compare_main([str(baseline), str(candidate), "--out", str(out)])
    assert rc == 0
    assert out.is_file()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert "summary" in data


def test_cli_missing_report_exits_nonzero(tmp_path):
    baseline = _write_report(tmp_path / "baseline.json", handler="Alpha158")
    rc = compare_main([str(baseline), str(tmp_path / "missing.json")])
    assert rc != 0


def test_cli_subprocess_smoke(tmp_path):
    baseline = _write_report(tmp_path / "baseline.json", handler="Alpha158")
    candidate = _write_report(
        tmp_path / "candidate.json", handler="MinedFactor",
        mean_information_ratio=0.45,
    )
    result = subprocess.run(
        [
            sys.executable, str(PROJECT_ROOT / "scripts" / "compare_factor_handlers.py"),
            str(baseline), str(candidate),
        ],
        capture_output=True, text=True,
        cwd=str(PROJECT_ROOT),
        timeout=60,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "Compare" in result.stdout
