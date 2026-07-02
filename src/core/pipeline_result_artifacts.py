"""Structured pipeline result artifacts for operator UI detail views.

The writer in this module projects already-produced pipeline outputs into a
stable set of UI-friendly files. It must not call qlib, model trainers, signal
analyzers, or metric helpers: official metric values are copied from
``CanonicalBacktestOutput.risk_analysis``.
"""

from __future__ import annotations

import json
import math
import platform
import shutil
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    import pandas as pd

from src.core._json_utils import _sanitize_for_json, sha256_canonical
from src.core.canonical_backtest_contract import CanonicalBacktestOutput

PIPELINE_RESULT_ARTIFACT_SCHEMA_VERSION = 1
TRADES_NOT_PRODUCED_REASON = "not_produced_by_canonical_runtime"
TRADE_COLUMNS = ("date", "stock", "side", "shares", "price", "amount", "cost")
PREDICTION_COLUMNS = ("date", "stock", "score")


class PipelineResultArtifactError(RuntimeError):
    """Raised when structured pipeline result artifacts cannot be written."""


def write_pipeline_result_artifacts(
    output_dir: Path,
    *,
    config: Any,
    backtest_output: CanonicalBacktestOutput,
    predictions: Any,
    started_at: str,
    report_path: str,
    model_artifact_path: str | None = None,
    stage_timings: Mapping[str, Any] | None = None,
    status: str = "completed",
    git_provenance: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Write structured pipeline artifacts and return their paths.

    The function is intentionally small in semantic scope: it serializes
    existing canonical outputs into dashboard-friendly files. It does not
    compute replacement official metrics.
    """

    output_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = output_dir / "logs"
    artifacts_dir = output_dir / "artifacts"
    logs_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    finished_at = datetime.now(tz=timezone.utc).isoformat()
    config_dict = _config_to_dict(config)
    config_hash = _stable_hash(config_dict)

    config_path = output_dir / "config.yaml"
    metrics_path = output_dir / "metrics.json"
    nav_path = output_dir / "nav.parquet"
    holdings_path = output_dir / "holdings.parquet"
    trades_path = output_dir / "trades.parquet"
    predictions_path = output_dir / "predictions.parquet"
    metadata_path = output_dir / "metadata.json"
    pipeline_log_path = logs_dir / "pipeline.log"
    stage_timings_path = logs_dir / "stage_timings.json"
    model_path = artifacts_dir / "model.pkl"

    nav_frame = _build_nav_frame(backtest_output.return_series)

    _write_yaml(config_path, config_dict)
    _write_json(
        metrics_path,
        _build_metrics(
            config_dict, backtest_output, nav_frame,
            started_at=started_at, finished_at=finished_at,
        ),
    )
    nav_frame.to_parquet(nav_path, index=False)
    _build_holdings_frame(backtest_output.positions).to_parquet(holdings_path, index=False)
    _build_empty_trades_frame().to_parquet(trades_path, index=False)
    _build_predictions_frame(predictions).to_parquet(predictions_path, index=False)
    _write_json(stage_timings_path, dict(stage_timings or {}))
    _write_text(
        pipeline_log_path,
        "Pipeline stdout/stderr is copied here for UI-launched jobs after the "
        "CLI exits. CLI-only runs may only have logger output in the console.\n",
    )
    if model_artifact_path:
        _copy_model_artifact(Path(model_artifact_path), model_path)

    metadata = _build_metadata(
        output_dir=output_dir,
        config_dict=config_dict,
        config_hash=config_hash,
        backtest_output=backtest_output,
        started_at=started_at,
        finished_at=finished_at,
        status=status,
        report_path=report_path,
        git_provenance=git_provenance,
        stage_timings=dict(stage_timings or {}),
        artifact_paths={
            "config": str(config_path),
            "metrics": str(metrics_path),
            "nav": str(nav_path),
            "holdings": str(holdings_path),
            "trades": str(trades_path),
            "predictions": str(predictions_path),
            "pipeline_report": str(report_path),
            "pipeline_log": str(pipeline_log_path),
            "stage_timings": str(stage_timings_path),
            "model": str(model_path),
        },
    )
    _write_json(metadata_path, metadata)

    return {
        "metadata": str(metadata_path),
        "metrics": str(metrics_path),
        "nav": str(nav_path),
        "holdings": str(holdings_path),
        "trades": str(trades_path),
        "predictions": str(predictions_path),
        "config": str(config_path),
        "pipeline_log": str(pipeline_log_path),
        "stage_timings": str(stage_timings_path),
        "model": str(model_path),
    }


def _config_to_dict(config: Any) -> dict[str, Any]:
    # ``is_dataclass`` returns True for both the class object AND
    # instances; ``asdict`` only accepts an instance. Narrow via
    # ``not isinstance(config, type)`` so a class object falls through
    # to the explicit error (matches the pattern in
    # ``walk_forward/_resume.py::compute_config_fingerprint``).
    if is_dataclass(config) and not isinstance(config, type):
        return dict(asdict(config))
    if isinstance(config, Mapping):
        return dict(config)
    raise PipelineResultArtifactError(
        f"Cannot serialize config of type {type(config).__name__}."
    )


def _stable_hash(payload: dict[str, Any]) -> str:
    return sha256_canonical(payload)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    sanitized = _sanitize_for_json(payload)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sanitized, f, indent=2, ensure_ascii=False, default=str, allow_nan=False)


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    sanitized = _sanitize_for_json(payload)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(sanitized, f, sort_keys=False, allow_unicode=True)


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _copy_model_artifact(source: Path, target: Path) -> None:
    if not source.is_file():
        raise PipelineResultArtifactError(
            f"model_artifact_path does not exist: {source}."
        )
    if source.resolve() == target.resolve():
        return
    shutil.copy2(source, target)


def _build_metrics(
    config: dict[str, Any],
    backtest_output: CanonicalBacktestOutput,
    nav_frame: Any,
    *,
    started_at: str | None = None,
    finished_at: str | None = None,
) -> dict[str, Any]:
    with_cost = _metric_section(backtest_output, "excess_return_with_cost")
    without_cost = _metric_section(backtest_output, "excess_return_without_cost")
    positions = backtest_output.positions or {}
    latest_holding_count = None
    if positions:
        latest_key = sorted(str(key) for key in positions.keys())[-1]
        latest = positions.get(latest_key)
        latest_holding_count = len(latest) if isinstance(latest, dict) else None

    # NAV-derived absolute annualised returns. ``_first_metric(with_cost,
    # "annual_return", "annualized_return")`` is the **excess** return —
    # historically (mis-)labelled ``annual_return`` in the artifact, which
    # operators read as the strategy's own gross/net annualised return.
    # We keep ``annual_return`` populated with the *excess* value for
    # backward-compat with already-written reports, but ADD explicit
    # strategy / benchmark fields below so the UI can render the right
    # number with the right label.
    strategy_total_return = _nav_total_return(nav_frame)
    benchmark_total_return = _nav_total_return_for(nav_frame, "benchmark_nav")
    n_trading_days = _nav_n_trading_days(nav_frame)
    strategy_annualised = _annualise_total_return(strategy_total_return, n_trading_days)
    benchmark_annualised = _annualise_total_return(benchmark_total_return, n_trading_days)

    return {
        "schema_version": PIPELINE_RESULT_ARTIFACT_SCHEMA_VERSION,
        "metric_status": backtest_output.metric_status,
        "official_backtest_path": backtest_output.official_backtest_path,
        "source": {
            "official_metrics": "CanonicalBacktestOutput.risk_analysis",
            "display_series": "CanonicalBacktestOutput.return_series",
        },
        "performance": {
            # Legacy field — same value as ``annual_excess_return_with_cost``.
            # Retained so existing readers don't break; new readers should
            # prefer ``strategy_annualized_return`` for the absolute number.
            "annual_return": _first_metric(with_cost, "annual_return", "annualized_return"),
            "total_return": strategy_total_return,
            "benchmark_total_return": benchmark_total_return,
            "strategy_annualized_return": strategy_annualised,
            "benchmark_annualized_return": benchmark_annualised,
            "n_trading_days": n_trading_days,
            "cumulative_nav_end": _nav_end(nav_frame, "strategy_nav"),
            "sharpe_ratio": _first_metric(with_cost, "sharpe", "sharpe_ratio"),
            "sortino_ratio": _first_metric(with_cost, "sortino", "sortino_ratio"),
            "information_ratio": with_cost.get("information_ratio"),
            "annual_excess_return_with_cost": with_cost.get("annualized_return"),
            "annual_excess_return_without_cost": without_cost.get("annualized_return"),
        },
        "risk": {
            "volatility_annualized": _first_metric(
                with_cost,
                "annualized_volatility",
                "volatility_annualized",
                "volatility",
            ),
            "max_drawdown": with_cost.get("max_drawdown"),
            "max_drawdown_duration_days": _first_metric(
                with_cost,
                "max_drawdown_duration_days",
                "max_drawdown_duration",
            ),
            "downside_deviation": _first_metric(with_cost, "downside_deviation"),
            "var_95": _first_metric(with_cost, "var_95", "VaR_95"),
            "calmar_ratio": _first_metric(with_cost, "calmar", "calmar_ratio"),
            "max_drawdown_without_cost": without_cost.get("max_drawdown"),
        },
        "trading": {
            "turnover_daily_avg": None,
            "turnover_annualized": None,
            "win_rate": None,
            "profit_loss_ratio": None,
            "avg_holding_days": None,
            "n_trades_total": 0,
            "n_trades_per_day_avg": None,
            "positions_days": len(positions),
            "latest_holding_count": latest_holding_count,
            "trades_status": TRADES_NOT_PRODUCED_REASON,
        },
        "benchmark": {
            "code": config.get("benchmark_code"),
            "annual_return": None,
            "max_drawdown": None,
            "alpha_annualized": with_cost.get("annualized_return"),
            "beta": None,
            "correlation": None,
        },
        "monthly_returns": _monthly_returns(nav_frame),
        "official_metrics": dict(backtest_output.risk_analysis),
        # ``timing`` block mirrors walk-forward's
        # ``aggregate_metrics["timing"]`` so shared consumers can
        # do ``report["timing"]["total_duration_seconds"]``
        # uniformly across engines. Codex P1 on PR #163.
        # Walk-forward-specific keys (``mean_fold_duration_seconds`` /
        # ``slowest_fold_*`` / ``valid_folds_duration``) live in the
        # walk-forward report only — the pipeline is single-fold by
        # construction so those values would be degenerate. The
        # ``total_duration_seconds`` field is comparable across
        # engines.
        "timing": _build_timing_block(
            started_at=started_at, finished_at=finished_at,
        ),
    }


def _build_timing_block(
    *, started_at: str | None, finished_at: str | None,
) -> dict[str, Any]:
    """Pipeline-shaped timing block — single-run duration only.

    Mirrors the walk-forward ``aggregate_metrics["timing"]`` sub-dict
    by namespace. Walk-forward-specific keys are intentionally
    absent (pipeline is single-fold; reporting them with degenerate
    values would mislead consumers into thinking they're aggregates).
    """
    duration = None
    if started_at and finished_at:
        duration_int = _duration_seconds(started_at, finished_at)
        if duration_int is not None:
            duration = float(duration_int)
    return {
        "total_duration_seconds": duration,
        "started_at": started_at,
        "finished_at": finished_at,
    }


def _first_metric(section: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        value = section.get(name)
        if value is not None:
            return value
    return None


def _nav_end(nav_frame: Any, column: str) -> float | None:
    if column not in nav_frame or nav_frame.empty:
        return None
    series = nav_frame[column].dropna()
    if series.empty:
        return None
    return _finite_float(series.iloc[-1], f"nav.{column}")


def _nav_total_return(nav_frame: Any) -> float | None:
    end = _nav_end(nav_frame, "strategy_nav")
    return None if end is None else end - 1.0


def _nav_total_return_for(nav_frame: Any, column: str) -> float | None:
    """Return cumulative NAV growth (end - 1.0) for the named NAV column.

    Used for benchmark NAV in addition to strategy. Returns ``None`` when
    the column is absent or all-NaN so callers can distinguish "no data"
    from "0 return"."""
    end = _nav_end(nav_frame, column)
    return None if end is None else end - 1.0


def _nav_n_trading_days(nav_frame: Any) -> int | None:
    """Count finite strategy-NAV rows.

    Used as the denominator when annualising NAV-derived returns. Falls
    back to ``None`` when the NAV frame is unusable so the caller can
    skip the annualisation rather than divide by zero."""
    if nav_frame is None or nav_frame.empty or "strategy_nav" not in nav_frame:
        return None
    n = int(nav_frame["strategy_nav"].dropna().shape[0])
    return n if n > 0 else None


# Trading days per calendar year — qlib convention. Centralised here so
# every NAV-derived annualisation in this module uses the same divisor.
_TRADING_DAYS_PER_YEAR = 252.0


def _annualise_total_return(
    total_return: float | None, n_trading_days: int | None
) -> float | None:
    """Convert a cumulative return into a geometric annual rate.

    Returns ``None`` when either input is missing or the window is too
    short to make annualisation meaningful (we keep the computation but
    rely on the UI's short-window banner to flag the instability). We
    skip non-finite intermediates loudly rather than coercing to 0 —
    AGENTS.md #8.
    """
    if total_return is None or n_trading_days is None:
        return None
    if n_trading_days <= 0:
        return None
    base = 1.0 + float(total_return)
    if base <= 0.0:
        # Total loss / impossible inputs — refuse to fabricate an
        # annualised rate; surface as None.
        return None
    try:
        # Explicit float() — ``float ** float`` can widen to ``Any``
        # under mypy because the result-type depends on operand signs
        # (a negative base + non-integer exponent can produce
        # ``complex``). ``base > 0`` here so we know the result is
        # finite float.
        return float(base ** (_TRADING_DAYS_PER_YEAR / float(n_trading_days)) - 1.0)
    except (ValueError, OverflowError):
        return None


def _monthly_returns(nav_frame: Any) -> list[dict[str, Any]]:
    if nav_frame.empty:
        return []
    frame = nav_frame.copy()
    frame["month"] = frame["date"].dt.strftime("%Y-%m")
    rows: list[dict[str, Any]] = []
    for month, group in frame.groupby("month", sort=True):
        strategy = _compound_return(group["strategy_return"])
        benchmark = (
            _compound_return(group["benchmark_return"].dropna())
            if "benchmark_return" in group
            else None
        )
        rows.append({
            "month": str(month),
            "strategy": strategy,
            "benchmark": benchmark,
        })
    return rows


def _compound_return(values: Any) -> float | None:
    cleaned: list[float] = []
    for value in values:
        if value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            cleaned.append(number)
    if not cleaned:
        return None
    product = 1.0
    for value in cleaned:
        product *= 1.0 + value
    return product - 1.0


def _metric_section(
    backtest_output: CanonicalBacktestOutput,
    key: str,
) -> dict[str, Any]:
    section = backtest_output.risk_analysis.get(key, {})
    return dict(section) if isinstance(section, Mapping) else {}


def _build_nav_frame(return_series: Any) -> pd.DataFrame:
    import pandas as pd

    returns = _mapping_to_series(return_series, "return", required=True)
    bench = _mapping_to_series(return_series, "bench", required=False)
    cost = _mapping_to_series(return_series, "cost", required=False)

    frame = pd.DataFrame({"strategy_return": returns})
    frame["strategy_nav"] = (1.0 + frame["strategy_return"]).cumprod()
    frame["strategy_drawdown"] = frame["strategy_nav"] / frame["strategy_nav"].cummax() - 1.0
    if bench is not None:
        frame["benchmark_return"] = bench.reindex(frame.index)
        frame["benchmark_nav"] = (1.0 + frame["benchmark_return"].fillna(0.0)).cumprod()
        frame["benchmark_drawdown"] = frame["benchmark_nav"] / frame["benchmark_nav"].cummax() - 1.0
    else:
        frame["benchmark_return"] = pd.NA
        frame["benchmark_nav"] = pd.NA
        frame["benchmark_drawdown"] = pd.NA
    if cost is not None:
        frame["cost"] = cost.reindex(frame.index)
    else:
        frame["cost"] = pd.NA

    frame.index.name = "date"
    return frame.reset_index()


def _mapping_to_series(
    return_series: Any, name: str, *, required: bool,
) -> pd.Series | None:
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


def _build_holdings_frame(positions: Any) -> pd.DataFrame:
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


def _build_empty_trades_frame() -> pd.DataFrame:
    import pandas as pd

    return pd.DataFrame(columns=list(TRADE_COLUMNS))


def _build_predictions_frame(predictions: Any) -> pd.DataFrame:
    import pandas as pd

    if not isinstance(predictions, pd.Series):
        raise PipelineResultArtifactError(
            "predictions must be a pandas Series before writing "
            f"predictions.parquet; got {type(predictions).__name__}."
        )
    if not isinstance(predictions.index, pd.MultiIndex):
        raise PipelineResultArtifactError(
            "predictions must have a (datetime, instrument) MultiIndex before "
            "writing predictions.parquet."
        )
    if not predictions.index.is_unique:
        raise PipelineResultArtifactError(
            "predictions index must be unique before writing predictions.parquet."
        )
    names = list(predictions.index.names)
    if "datetime" not in names or "instrument" not in names:
        raise PipelineResultArtifactError(
            "predictions index must contain 'datetime' and 'instrument' levels; "
            f"got {names!r}."
        )

    frame = predictions.rename("score").reset_index()
    frame = frame.rename(columns={"datetime": "date", "instrument": "stock"})
    frame = frame[["date", "stock", "score"]].copy()
    frame["date"] = pd.to_datetime(frame["date"])
    frame["stock"] = frame["stock"].astype(str)
    frame["score"] = [
        _finite_float(value, "predictions.score")
        for value in frame["score"].tolist()
    ]
    return frame.sort_values(["date", "stock"], kind="stable").reset_index(drop=True)


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
    stage_timings: dict[str, Any],
    artifact_paths: dict[str, str],
    git_provenance: Mapping[str, Any] | None = None,
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
        "host": platform.node(),
        "qlib_version": _qlib_version(),
        # The SAME run-start capture pipeline_report.json records (full sha) — NOT a
        # second write-time probe: if HEAD advances mid-run, two independent captures
        # would leave the run directory with two competing git_commit values and break
        # any metadata-based ancestor reasoning (codex P2 on #313, round 5).
        "git_commit": (git_provenance or {}).get("commit"),
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
        "stage_timings": stage_timings,
    }


def _qlib_version() -> str | None:
    """Return the installed qlib version, or ``None`` if unavailable.

    Previously: ``str(getattr(qlib, "__version__", "") or None)``. That
    expression has a subtle Python-semantics trap — when
    ``qlib.__version__`` is the empty string ``""``, the inner
    ``"" or None`` evaluates to ``None`` (truthiness), then the outer
    ``str(None)`` produces the **string literal** ``"None"``. JSON
    serialisation then writes ``"None"`` into the artifact instead of
    ``null``, masking the fact that the version is actually absent
    and tripping any downstream comparison logic that treats
    ``"None"`` as a valid version string. (bug.md P1-7.)
    """
    try:
        import qlib
    except ImportError:
        return None
    version = getattr(qlib, "__version__", "")
    return version if version else None


def _duration_seconds(started_at: str, finished_at: str) -> int | None:
    try:
        start = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        finish = datetime.fromisoformat(str(finished_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, int((finish - start).total_seconds()))
