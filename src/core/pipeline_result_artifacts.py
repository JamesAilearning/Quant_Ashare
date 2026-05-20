"""Structured pipeline result artifacts for operator UI detail views.

The writer in this module projects already-produced pipeline outputs into a
stable set of UI-friendly files. It must not call qlib, model trainers, signal
analyzers, or metric helpers: official metric values are copied from
``CanonicalBacktestOutput.risk_analysis``.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from src.core._json_utils import _sanitize_for_json
from src.core.canonical_backtest_contract import CanonicalBacktestOutput

PIPELINE_RESULT_ARTIFACT_SCHEMA_VERSION = 1
TRADES_NOT_PRODUCED_REASON = "not_produced_by_canonical_runtime"
TRADE_COLUMNS = ("date", "stock", "side", "shares", "price", "amount", "cost")


class PipelineResultArtifactError(RuntimeError):
    """Raised when structured pipeline result artifacts cannot be written."""


def write_pipeline_result_artifacts(
    output_dir: Path,
    *,
    config: Any,
    backtest_output: CanonicalBacktestOutput,
    started_at: str,
    report_path: str,
    status: str = "completed",
) -> dict[str, str]:
    """Write structured pipeline artifacts and return their paths.

    The function is intentionally small in semantic scope: it serializes
    existing canonical outputs into dashboard-friendly files. It does not
    compute replacement official metrics.
    """

    output_dir.mkdir(parents=True, exist_ok=True)
    finished_at = datetime.now(tz=timezone.utc).isoformat()
    config_dict = _config_to_dict(config)
    config_hash = _stable_hash(config_dict)

    config_path = output_dir / "config.yaml"
    metrics_path = output_dir / "metrics.json"
    nav_path = output_dir / "nav.parquet"
    holdings_path = output_dir / "holdings.parquet"
    trades_path = output_dir / "trades.parquet"
    metadata_path = output_dir / "metadata.json"

    _write_yaml(config_path, config_dict)
    _write_json(metrics_path, _build_metrics(config_dict, backtest_output))
    _build_nav_frame(backtest_output.return_series).to_parquet(nav_path, index=False)
    _build_holdings_frame(backtest_output.positions).to_parquet(holdings_path, index=False)
    _build_empty_trades_frame().to_parquet(trades_path, index=False)

    metadata = _build_metadata(
        output_dir=output_dir,
        config_dict=config_dict,
        config_hash=config_hash,
        backtest_output=backtest_output,
        started_at=started_at,
        finished_at=finished_at,
        status=status,
        report_path=report_path,
        artifact_paths={
            "config": str(config_path),
            "metrics": str(metrics_path),
            "nav": str(nav_path),
            "holdings": str(holdings_path),
            "trades": str(trades_path),
            "pipeline_report": str(report_path),
        },
    )
    _write_json(metadata_path, metadata)

    return {
        "metadata": str(metadata_path),
        "metrics": str(metrics_path),
        "nav": str(nav_path),
        "holdings": str(holdings_path),
        "trades": str(trades_path),
        "config": str(config_path),
    }


def _config_to_dict(config: Any) -> dict[str, Any]:
    if is_dataclass(config):
        return dict(asdict(config))
    if isinstance(config, Mapping):
        return dict(config)
    raise PipelineResultArtifactError(
        f"Cannot serialize config of type {type(config).__name__}."
    )


def _stable_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    sanitized = _sanitize_for_json(payload)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sanitized, f, indent=2, ensure_ascii=False, default=str, allow_nan=False)


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    sanitized = _sanitize_for_json(payload)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(sanitized, f, sort_keys=False, allow_unicode=True)


def _build_metrics(
    config: dict[str, Any],
    backtest_output: CanonicalBacktestOutput,
) -> dict[str, Any]:
    with_cost = _metric_section(backtest_output, "excess_return_with_cost")
    without_cost = _metric_section(backtest_output, "excess_return_without_cost")
    positions = backtest_output.positions or {}
    latest_holding_count = None
    if positions:
        latest_key = sorted(str(key) for key in positions.keys())[-1]
        latest = positions.get(latest_key)
        latest_holding_count = len(latest) if isinstance(latest, dict) else None

    return {
        "schema_version": PIPELINE_RESULT_ARTIFACT_SCHEMA_VERSION,
        "metric_status": backtest_output.metric_status,
        "official_backtest_path": backtest_output.official_backtest_path,
        "source": {
            "official_metrics": "CanonicalBacktestOutput.risk_analysis",
            "display_series": "CanonicalBacktestOutput.return_series",
        },
        "performance": {
            "annual_excess_return_with_cost": with_cost.get("annualized_return"),
            "annual_excess_return_without_cost": without_cost.get("annualized_return"),
            "information_ratio": with_cost.get("information_ratio"),
        },
        "risk": {
            "max_drawdown": with_cost.get("max_drawdown"),
            "max_drawdown_without_cost": without_cost.get("max_drawdown"),
        },
        "trading": {
            "positions_days": len(positions),
            "latest_holding_count": latest_holding_count,
            "trades_status": TRADES_NOT_PRODUCED_REASON,
        },
        "benchmark": {
            "code": config.get("benchmark_code"),
        },
        "official_metrics": dict(backtest_output.risk_analysis),
    }


def _metric_section(
    backtest_output: CanonicalBacktestOutput,
    key: str,
) -> dict[str, Any]:
    section = backtest_output.risk_analysis.get(key, {})
    return dict(section) if isinstance(section, Mapping) else {}


def _build_nav_frame(return_series: Any):
    import pandas as pd

    returns = _mapping_to_series(return_series, "return", required=True)
    bench = _mapping_to_series(return_series, "bench", required=False)
    cost = _mapping_to_series(return_series, "cost", required=False)

    frame = pd.DataFrame({"strategy_return": returns})
    frame["strategy_nav"] = (1.0 + frame["strategy_return"]).cumprod()
    if bench is not None:
        frame["benchmark_return"] = bench.reindex(frame.index)
        frame["benchmark_nav"] = (1.0 + frame["benchmark_return"].fillna(0.0)).cumprod()
    else:
        frame["benchmark_return"] = pd.NA
        frame["benchmark_nav"] = pd.NA
    if cost is not None:
        frame["cost"] = cost.reindex(frame.index)
    else:
        frame["cost"] = pd.NA

    frame.index.name = "date"
    return frame.reset_index()


def _mapping_to_series(return_series: Any, name: str, *, required: bool):
    import pandas as pd

    if not isinstance(return_series, Mapping):
        raise PipelineResultArtifactError(
            "return_series must be a mapping before writing nav.parquet; "
            f"got {type(return_series).__name__}."
        )
    payload = return_series.get(name)
    if not payload:
        if required:
            raise PipelineResultArtifactError(
                f"return_series[{name!r}] is required for nav.parquet."
            )
        return None
    if not isinstance(payload, Mapping):
        raise PipelineResultArtifactError(
            f"return_series[{name!r}] must be a mapping; got "
            f"{type(payload).__name__}."
        )

    series = pd.Series(
        {pd.Timestamp(key): _finite_float(value, f"return_series[{name!r}]") for key, value in payload.items()},
        dtype="float64",
    ).sort_index()
    if series.empty and required:
        raise PipelineResultArtifactError(
            f"return_series[{name!r}] is empty after parsing."
        )
    return series


def _finite_float(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise PipelineResultArtifactError(
            f"{label} contains a non-numeric value: {value!r}."
        ) from exc
    if not math.isfinite(number):
        raise PipelineResultArtifactError(
            f"{label} contains a non-finite value: {value!r}."
        )
    return number


def _build_holdings_frame(positions: Any):
    import pandas as pd

    rows: list[dict[str, Any]] = []
    if positions:
        if not isinstance(positions, Mapping):
            raise PipelineResultArtifactError(
                "positions must be a mapping before writing holdings.parquet; "
                f"got {type(positions).__name__}."
            )
        for raw_date_key, day_positions in sorted(positions.items(), key=lambda item: str(item[0])):
            date_key = str(raw_date_key)
            if not isinstance(day_positions, Mapping):
                raise PipelineResultArtifactError(
                    f"positions[{date_key!r}] must be a mapping; got "
                    f"{type(day_positions).__name__}."
                )
            parsed_items = [
                (
                    instrument,
                    _finite_float(weight, f"positions[{date_key!r}][{instrument!r}]"),
                )
                for instrument, weight in day_positions.items()
            ]
            sorted_items = sorted(parsed_items, key=lambda item: item[1], reverse=True)
            for rank, (instrument, weight) in enumerate(sorted_items, start=1):
                rows.append({
                    "date": pd.Timestamp(date_key),
                    "stock": str(instrument),
                    "weight": weight,
                    "rank": rank,
                })
    return pd.DataFrame(rows, columns=["date", "stock", "weight", "rank"])


def _build_empty_trades_frame():
    import pandas as pd

    return pd.DataFrame(columns=list(TRADE_COLUMNS))


def _build_metadata(
    *,
    output_dir: Path,
    config_dict: dict[str, Any],
    config_hash: str,
    backtest_output: CanonicalBacktestOutput,
    started_at: str,
    finished_at: str,
    status: str,
    report_path: str,
    artifact_paths: dict[str, str],
) -> dict[str, Any]:
    return {
        "schema_version": PIPELINE_RESULT_ARTIFACT_SCHEMA_VERSION,
        "run_id": output_dir.name,
        "type": "pipeline",
        "status": status,
        "created_at": started_at,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_seconds": _duration_seconds(started_at, finished_at),
        "config_hash": config_hash,
        "metric_status": backtest_output.metric_status,
        "official_backtest_path": backtest_output.official_backtest_path,
        "config_summary": {
            "instruments": config_dict.get("instruments"),
            "feature_handler": config_dict.get("feature_handler"),
            "model_type": config_dict.get("model_type"),
            "benchmark_code": config_dict.get("benchmark_code"),
            "topk": config_dict.get("topk"),
            "n_drop": config_dict.get("n_drop"),
        },
        "report_path": report_path,
        "artifact_paths": artifact_paths,
        "trade_log_status": TRADES_NOT_PRODUCED_REASON,
        "stage_timings": {},
    }


def _duration_seconds(started_at: str, finished_at: str) -> int | None:
    try:
        start = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        finish = datetime.fromisoformat(str(finished_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, int((finish - start).total_seconds()))
