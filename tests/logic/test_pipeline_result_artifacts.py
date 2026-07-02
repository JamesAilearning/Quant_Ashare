"""Tests for ``src.core.pipeline_result_artifacts``.

The module serializes Pipeline outputs to dashboard-friendly artifact
files (config.yaml, metrics.json, nav.parquet, holdings.parquet,
trades.parquet, predictions.parquet, metadata.json, logs/). We cover
the public surface dimensionally:

- `_config_to_dict`: dataclass / mapping / invalid type
- `_stable_hash`: deterministic across key reorder
- `_finite_float`: finite / NaN / inf / non-numeric
- `_compound_return`: empty / single value / multi-value / NaN entries
- `_nav_total_return`: empty / single-day
- `write_pipeline_result_artifacts`: smoke end-to-end with a synthetic
  CanonicalBacktestOutput

We do NOT exercise model.pkl copy with a real qlib model — that goes
to E2E. We do not exercise git/qlib version helpers — they're
environment-dependent and tested by their absence-handling paths.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.canonical_backtest_contract import (  # noqa: E402
    CanonicalBacktestOutput,
)
from src.core.pipeline_result_artifacts import (  # noqa: E402
    PipelineResultArtifactError,
    _compound_return,
    _config_to_dict,
    _finite_float,
    _nav_total_return,
    _qlib_version,
    _stable_hash,
    write_pipeline_result_artifacts,
)

# ---------------------------------------------------------------------------
# _config_to_dict
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _TinyConfig:
    a: int = 1
    b: str = "x"


def test_config_to_dict_dataclass():
    out = _config_to_dict(_TinyConfig(a=42, b="hi"))
    assert out == {"a": 42, "b": "hi"}


def test_config_to_dict_mapping():
    out = _config_to_dict({"k1": 1, "k2": 2})
    assert out == {"k1": 1, "k2": 2}


def test_config_to_dict_invalid_raises():
    with pytest.raises(PipelineResultArtifactError):
        _config_to_dict("not a config")


# ---------------------------------------------------------------------------
# _stable_hash
# ---------------------------------------------------------------------------


def test_stable_hash_is_deterministic_across_key_reorder():
    a = _stable_hash({"x": 1, "y": 2})
    b = _stable_hash({"y": 2, "x": 1})
    assert a == b


def test_stable_hash_changes_when_values_change():
    a = _stable_hash({"x": 1})
    b = _stable_hash({"x": 2})
    assert a != b


# ---------------------------------------------------------------------------
# _finite_float
# ---------------------------------------------------------------------------


def test_finite_float_valid():
    assert _finite_float(0.5, "x") == 0.5
    assert _finite_float(0, "x") == 0.0
    assert _finite_float("1.5", "x") == 1.5


def test_finite_float_nan_raises():
    with pytest.raises(PipelineResultArtifactError, match="non-finite"):
        _finite_float(float("nan"), "x")


def test_finite_float_inf_raises():
    with pytest.raises(PipelineResultArtifactError, match="non-finite"):
        _finite_float(float("inf"), "x")


def test_finite_float_non_numeric_raises():
    with pytest.raises(PipelineResultArtifactError, match="non-numeric"):
        _finite_float("hello", "x")


# ---------------------------------------------------------------------------
# _compound_return
# ---------------------------------------------------------------------------


def test_compound_return_empty():
    assert _compound_return([]) is None


def test_compound_return_all_skipped():
    # All NaN / None / non-numeric → no cleaned values → None
    assert _compound_return([float("nan"), None, "abc"]) is None


def test_compound_return_single_value():
    assert _compound_return([0.05]) == pytest.approx(0.05)


def test_compound_return_multi_value():
    # (1+0.01)*(1+0.02)*(1+0.03) - 1
    expected = 1.01 * 1.02 * 1.03 - 1
    assert _compound_return([0.01, 0.02, 0.03]) == pytest.approx(expected)


def test_compound_return_skips_invalid_entries():
    # Same answer as if NaN were absent
    expected = 1.01 * 1.02 - 1
    assert _compound_return([0.01, float("nan"), 0.02, None]) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# _nav_total_return
# ---------------------------------------------------------------------------


def test_nav_total_return_empty_frame():
    empty = pd.DataFrame({"strategy_nav": []})
    assert _nav_total_return(empty) is None


def test_nav_total_return_single_day():
    frame = pd.DataFrame({"strategy_nav": [1.05]})
    assert _nav_total_return(frame) == pytest.approx(0.05)


def test_nav_total_return_missing_column():
    frame = pd.DataFrame({"other_col": [1.0]})
    assert _nav_total_return(frame) is None


# ---------------------------------------------------------------------------
# write_pipeline_result_artifacts — end-to-end smoke
# ---------------------------------------------------------------------------


def _make_backtest_output() -> CanonicalBacktestOutput:
    """Minimal backtest output that produces a valid nav frame."""
    return CanonicalBacktestOutput(
        metric_status="ok",
        official_backtest_path="output/wf/canonical/backtest.csv",
        return_series={
            "return": {
                "2024-01-01": 0.01,
                "2024-01-02": -0.005,
                "2024-01-03": 0.008,
            },
            "bench": {
                "2024-01-01": 0.005,
                "2024-01-02": -0.002,
                "2024-01-03": 0.003,
            },
        },
        risk_analysis={
            "excess_return_with_cost": {
                "annualized_return": 0.12,
                "max_drawdown": -0.08,
                "information_ratio": 0.5,
            },
        },
        report={},
        provenance={},
        positions={
            "2024-01-01": {"SH600000": 0.5, "SH600001": 0.5},
            "2024-01-02": {"SH600000": 0.6, "SH600001": 0.4},
        },
    )


def test_write_pipeline_result_artifacts_writes_all_files(tmp_path):
    backtest = _make_backtest_output()
    predictions = pd.Series(
        [0.1, 0.2, 0.3],
        index=pd.MultiIndex.from_product(
            [pd.to_datetime(["2024-01-03"]), ["SH600000", "SH600001", "SH600002"]],
            names=["datetime", "instrument"],
        ),
        name="score",
    )
    out = write_pipeline_result_artifacts(
        tmp_path / "out",
        config=_TinyConfig(),
        backtest_output=backtest,
        predictions=predictions,
        started_at="2024-01-01T00:00:00+00:00",
        report_path="output/wf/pipeline_report.json",
    )
    # All declared artifacts exist on disk, EXCEPT model.pkl which
    # is only copied when ``model_artifact_path`` is provided.
    for key, path_value in out.items():
        if key == "model":
            continue
        assert Path(path_value).exists(), f"missing artifact: {key} → {path_value}"
    # Metadata round-trips
    meta = json.loads((tmp_path / "out" / "metadata.json").read_text(encoding="utf-8"))
    assert meta["status"] == "completed"
    assert "config_hash" in meta
    assert meta["artifact_paths"]["metrics"].endswith("metrics.json")


def test_write_pipeline_result_artifacts_metrics_section(tmp_path):
    backtest = _make_backtest_output()
    predictions = pd.Series(
        [0.1, 0.2], index=pd.MultiIndex.from_product(
            [pd.to_datetime(["2024-01-03"]), ["A", "B"]],
            names=["datetime", "instrument"],
        ),
    )
    write_pipeline_result_artifacts(
        tmp_path / "out", config=_TinyConfig(),
        backtest_output=backtest, predictions=predictions,
        started_at="2024-01-01T00:00:00+00:00",
        report_path="report.json",
    )
    metrics = json.loads((tmp_path / "out" / "metrics.json").read_text(encoding="utf-8"))
    # The metrics block surfaces cost-adjusted aggregates under
    # ``performance`` / ``risk`` namespaces (not a raw `with_cost`
    # dict — that lives under `official_metrics`).
    assert metrics["performance"]["annual_excess_return_with_cost"] == pytest.approx(0.12)
    assert metrics["risk"]["max_drawdown"] == pytest.approx(-0.08)
    # The raw payload is also preserved under official_metrics for
    # consumers that want the qlib-shaped dict back.
    assert metrics["official_metrics"]["excess_return_with_cost"]["information_ratio"] == pytest.approx(0.5)


def test_write_pipeline_result_artifacts_holdings_parquet(tmp_path):
    backtest = _make_backtest_output()
    predictions = pd.Series(
        [0.1, 0.2], index=pd.MultiIndex.from_product(
            [pd.to_datetime(["2024-01-03"]), ["A", "B"]],
            names=["datetime", "instrument"],
        ),
    )
    write_pipeline_result_artifacts(
        tmp_path / "out", config=_TinyConfig(),
        backtest_output=backtest, predictions=predictions,
        started_at="2024-01-01T00:00:00+00:00",
        report_path="report.json",
    )
    holdings = pd.read_parquet(tmp_path / "out" / "holdings.parquet")
    # Two days × two instruments = 4 rows.
    assert len(holdings) == 4
    # Each day's weights sum to ~1.0
    for _, group in holdings.groupby(holdings.columns[0]):
        weights = group[holdings.columns[2]] if "weight" in holdings.columns[2] \
            else group.select_dtypes("number").iloc[:, -1]
        assert weights.sum() == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Regression for bug.md P1-7: ``_qlib_version`` returned the **string**
# ``"None"`` when ``qlib.__version__`` was ``""``. ``str("" or None)``
# evaluates to ``str(None) == "None"`` — Python truthiness on the
# empty string falls through to ``None``, then ``str`` rebuilds it as
# the literal four-character string. JSON serialisation then writes
# ``"None"`` into the artifact, which downstream version-comparison
# logic would treat as a real version.
# ---------------------------------------------------------------------------


class _FakeQlibModule:
    """Stand-in for the ``qlib`` package whose ``__version__`` we
    control per-test."""

    def __init__(self, version):
        self.__version__ = version


def test_qlib_version_returns_python_none_for_empty_string(monkeypatch):
    """The literal failure mode bug.md flagged: empty-string
    ``__version__`` MUST return Python ``None``, not the string
    ``"None"``. JSON would serialise the former as ``null`` and the
    latter as the four-character string."""
    fake = _FakeQlibModule(version="")
    monkeypatch.setitem(sys.modules, "qlib", fake)
    out = _qlib_version()
    assert out is None, (
        f"P1-7 regression: empty __version__ should return None, got {out!r}"
    )


def test_qlib_version_returns_python_none_for_missing_attr(monkeypatch):
    """If the qlib module exists but lacks ``__version__`` entirely,
    treat as absent (not as a coerced sentinel)."""
    fake = type("FakeNoVer", (), {})()  # bare object, no __version__
    monkeypatch.setitem(sys.modules, "qlib", fake)
    out = _qlib_version()
    assert out is None


def test_qlib_version_returns_string_for_real_version(monkeypatch):
    """A normal install returns the actual version string verbatim."""
    fake = _FakeQlibModule(version="0.9.6")
    monkeypatch.setitem(sys.modules, "qlib", fake)
    out = _qlib_version()
    assert out == "0.9.6"


def test_qlib_version_returns_none_when_qlib_not_importable(monkeypatch):
    """When qlib is genuinely absent (operator env without it),
    the helper returns ``None`` instead of raising ImportError."""
    # Simulate a hostile import — set sys.modules["qlib"] to a class
    # that raises on attribute access? Cleaner: remove if present and
    # block fresh imports by patching the finder. Simplest: patch the
    # function's import inside the helper. Since the import is inside
    # the function body, we can use monkeypatch on builtins.__import__.
    real_import = __builtins__["__import__"] if isinstance(
        __builtins__, dict,
    ) else __builtins__.__import__

    def fake_import(name, *args, **kwargs):
        if name == "qlib":
            raise ImportError("qlib not installed (simulated)")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    # Also clear any already-imported qlib so the function actually
    # invokes __import__ rather than reading from sys.modules.
    monkeypatch.delitem(sys.modules, "qlib", raising=False)
    assert _qlib_version() is None


def test_metadata_git_commit_is_the_injected_run_start_capture(tmp_path):
    """codex P2 on #313 round 5: metadata.json must record the SAME run-start
    git provenance as pipeline_report.json — never a second write-time probe,
    which could disagree if HEAD advances mid-run. Omitted -> null (no probe)."""
    backtest = _make_backtest_output()
    predictions = pd.Series(
        [0.1, 0.2],
        index=pd.MultiIndex.from_product(
            [pd.to_datetime(["2024-01-03"]), ["SH600000", "SH600001"]],
            names=["datetime", "instrument"],
        ),
        name="score",
    )
    write_pipeline_result_artifacts(
        tmp_path / "with_gp",
        config=_TinyConfig(),
        backtest_output=backtest,
        predictions=predictions,
        started_at="2024-01-01T00:00:00+00:00",
        report_path="output/wf/pipeline_report.json",
        git_provenance={"commit": "cafebabe" * 5, "dirty": False},
    )
    meta = json.loads((tmp_path / "with_gp" / "metadata.json").read_text(encoding="utf-8"))
    assert meta["git_commit"] == "cafebabe" * 5

    write_pipeline_result_artifacts(
        tmp_path / "without_gp",
        config=_TinyConfig(),
        backtest_output=backtest,
        predictions=predictions,
        started_at="2024-01-01T00:00:00+00:00",
        report_path="output/wf/pipeline_report.json",
    )
    meta2 = json.loads((tmp_path / "without_gp" / "metadata.json").read_text(encoding="utf-8"))
    assert meta2["git_commit"] is None
