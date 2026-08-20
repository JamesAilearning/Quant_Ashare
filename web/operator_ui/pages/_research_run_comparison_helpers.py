"""Read models and pure comparison gates for the research comparison page.

This module intentionally does not import Streamlit or read files.  Artifact
loading belongs to the page boundary; the transformation below consumes only
already-read YAML/JSON mappings, which keeps the conservative comparison gate
easy to test and prevents this view from becoming another metrics engine.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from src.core.canonical_backtest_contract import OFFICIAL_METRIC_STATUS
from web.operator_ui.job_io import JobSummary, fold_catalog_by_dir

CONTRACT_FIELDS: tuple[tuple[str, str], ...] = (
    ("engine", "研究运行类型"),
    ("universe", "股票池 / universe"),
    ("training_window", "训练窗口"),
    ("validation_window", "验证窗口"),
    ("testing_window", "测试窗口"),
    ("benchmark", "回测基准"),
    ("execution_lag", "信号至成交滞后"),
    ("exchange_cost", "交易所与成本模型"),
    ("st_mask_identity", "ST 掩码输入内容"),
    ("data_provenance", "数据来源 / 运行时快照"),
)


@dataclass(frozen=True)
class ComparisonIssue:
    """A visible reason a historical run cannot support comparison."""

    code: str
    message: str


@dataclass(frozen=True)
class ReportMetric:
    """A scalar already written by a report, with its exact source label."""

    label: str
    value: float | None
    source: str


@dataclass(frozen=True)
class FoldEvidence:
    """Existing walk-forward evidence; no values here are recomputed."""

    fold_count: int | None
    valid_fold_count: int | None
    mean_information_ratio: float | None
    std_information_ratio: float | None
    worst_drawdown: float | None
    test_window_coverage: Mapping[str, Any]
    folds: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class ComparisonRun:
    """One selected run transformed from its existing artifacts."""

    run_id: str
    engine: str
    status: str
    created_at: str
    config_path: str
    report_path: str
    log_paths: tuple[str, ...]
    contract: Mapping[str, str | None]
    metrics: tuple[ReportMetric, ...]
    metric_status: str | None
    model_identity: str | None
    config_identity: str | None
    data_package_version: str | None
    data_provenance_source: str | None
    fold_evidence: FoldEvidence | None
    issues: tuple[ComparisonIssue, ...]


@dataclass(frozen=True)
class ComparabilityResult:
    """The all-or-nothing outcome for a selected collection of runs."""

    eligible: bool
    reasons: tuple[str, ...]
    ranked_run_ids: tuple[str, ...]


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _nested(mapping: Mapping[str, Any], *keys: str) -> Any:
    current: Any = mapping
    for key in keys:
        if not isinstance(current, Mapping) or key not in current:
            return None
        current = current[key]
    return current


def _finite_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    return None


def _stable_value(value: Any) -> str | None:
    """Create a deterministic display/comparison value without inference."""
    if value is None or value == "":
        return None
    if isinstance(value, Mapping):
        if not value:
            return None
        return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    if isinstance(value, (list, tuple)):
        if not value:
            return None
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def _window_from_pipeline(config: Mapping[str, Any], key: str) -> str | None:
    return _stable_value(config.get(key))


def _window_from_walk_forward(config: Mapping[str, Any], kind: str) -> str | None:
    start = _stable_value(config.get("overall_start"))
    end = _stable_value(config.get("overall_end"))
    months = _stable_value(config.get(f"{kind}_months"))
    if not (start and end and months):
        return None
    return f"{start} ~ {end}; {kind}_months={months}"


def _exchange_cost_from_request(request: Mapping[str, Any]) -> str | None:
    exchange = _mapping(request.get("exchange_config"))
    cost = _mapping(exchange.get("cost_model"))
    value = {
        "execution_price_kind": exchange.get("execution_price_kind"),
        "commission_rate": cost.get("commission_rate"),
        "stamp_tax_schedule": cost.get("stamp_tax_schedule"),
        "slippage_bps": cost.get("slippage_bps"),
        "min_cost": cost.get("min_cost"),
        "limit_threshold": exchange.get("limit_threshold"),
        "adjust_mode": request.get("adjust_mode"),
    }
    if any(item is None for item in value.values()):
        return None
    return _stable_value(value)


def _exchange_cost_from_config(config: Mapping[str, Any]) -> str | None:
    keys = (
        "execution_price_kind",
        "commission_rate",
        "stamp_tax_schedule",
        "slippage_bps",
        "min_cost",
        "limit_threshold",
        "adjust_mode",
    )
    # ``stamp_tax_schedule: null`` is an explicit, producer-recorded default
    # that the canonical request resolves deterministically.  Its *absence*
    # is what leaves the comparison unconfirmable.
    if any(key not in config for key in keys):
        return None
    value = {key: config[key] for key in keys}
    return _stable_value(value)


def _data_provenance(runtime: Mapping[str, Any]) -> str | None:
    """Return a producer-recorded runtime snapshot, never a YAML substitute."""
    if not runtime:
        return None
    required = ("provider_uri", "region", "data_adjust_mode")
    if any(not runtime.get(key) for key in required):
        return None
    return _stable_value({key: runtime[key] for key in required})


def _execution_lag(value: Any) -> str | None:
    """Accept only the canonical total signal-to-fill delay (an int >= 1)."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return str(value)


def _st_mask_identity(provenance: Mapping[str, Any]) -> str | None:
    """Return the recorded ST-input content hash, never its mutable path."""
    sha256 = _mapping(provenance.get("st_mask")).get("namechange_sha256")
    if not isinstance(sha256, str):
        return None
    normalized = sha256.strip().lower()
    if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
        return None
    return normalized


def _pipeline_contract(report: Mapping[str, Any]) -> dict[str, str | None]:
    report_config = _mapping(report.get("config"))
    provenance = _mapping(_nested(report, "backtest", "provenance", "config"))
    runtime = _mapping(provenance.get("runtime"))
    return {
        "engine": "pipeline",
        "universe": _stable_value(report_config.get("instruments")),
        "training_window": _window_from_pipeline(report_config, "train_period"),
        "validation_window": _window_from_pipeline(report_config, "valid_period"),
        "testing_window": _window_from_pipeline(report_config, "test_period"),
        "benchmark": _stable_value(provenance.get("benchmark_code")),
        "execution_lag": _execution_lag(provenance.get("signal_to_execution_lag")),
        "exchange_cost": _exchange_cost_from_request(provenance),
        "st_mask_identity": _st_mask_identity(provenance),
        "data_provenance": _data_provenance(runtime),
    }


def _walk_forward_contract(
    report: Mapping[str, Any], config: Mapping[str, Any]
) -> dict[str, str | None]:
    report_config = _mapping(report.get("config"))
    source = report_config if report_config else config
    return {
        "engine": "walk_forward",
        "universe": _stable_value(source.get("instruments")),
        "training_window": _window_from_walk_forward(source, "train"),
        "validation_window": _window_from_walk_forward(source, "valid"),
        "testing_window": _stable_value(report.get("test_window_coverage")),
        "benchmark": _stable_value(source.get("benchmark_code")),
        "execution_lag": _execution_lag(source.get("signal_to_execution_lag")),
        "exchange_cost": _exchange_cost_from_config(source),
        # Fold reports already record their ST-mask hashes, but the aggregate
        # walk-forward artifact does not expose one run-level identity yet.
        # Keep it unavailable rather than inferring from config.yaml.
        "st_mask_identity": None,
        # Existing walk-forward reports do not record the initialized qlib
        # runtime.  A config.yaml declaration is not evidence of the runtime
        # used, so ordering must remain blocked until a producer emits it.
        "data_provenance": None,
    }


def _pipeline_metrics(report: Mapping[str, Any]) -> tuple[ReportMetric, ...]:
    risk = _mapping(_nested(report, "risk_analysis", "excess_return_with_cost"))
    return (
        ReportMetric("年化超额（扣费后）", _finite_float(risk.get("annualized_return")),
                     "pipeline_report.json:risk_analysis.excess_return_with_cost.annualized_return"),
        ReportMetric("最大回撤（扣费后超额）", _finite_float(risk.get("max_drawdown")),
                     "pipeline_report.json:risk_analysis.excess_return_with_cost.max_drawdown"),
        ReportMetric("信息比率（扣费后超额）", _finite_float(risk.get("information_ratio")),
                     "pipeline_report.json:risk_analysis.excess_return_with_cost.information_ratio"),
    )


def _walk_forward_metrics(report: Mapping[str, Any]) -> tuple[ReportMetric, ...]:
    aggregate = _mapping(report.get("aggregate_metrics"))
    return (
        ReportMetric("平均年化超额（扣费后）", _finite_float(aggregate.get("mean_annualized_return")),
                     "walk_forward_report.json:aggregate_metrics.mean_annualized_return"),
        ReportMetric("最差超额回撤", _finite_float(aggregate.get("worst_drawdown")),
                     "walk_forward_report.json:aggregate_metrics.worst_drawdown"),
        ReportMetric("平均信息比率（扣费后超额）", _finite_float(aggregate.get("mean_information_ratio")),
                     "walk_forward_report.json:aggregate_metrics.mean_information_ratio"),
    )


def _effective_metric_status(report: Mapping[str, Any]) -> str | None:
    """Return the producer-recorded status, letting declared purpose downgrade it."""
    metric_status = _stable_value(report.get("metric_status"))
    metrics_purpose = _stable_value(report.get("metrics_purpose"))
    if (
        metric_status == OFFICIAL_METRIC_STATUS
        and metrics_purpose is not None
        and metrics_purpose != OFFICIAL_METRIC_STATUS
    ):
        return metrics_purpose
    return metric_status


def _walk_forward_evidence(report: Mapping[str, Any]) -> FoldEvidence:
    aggregate = _mapping(report.get("aggregate_metrics"))
    raw_folds = report.get("folds")
    folds = tuple(item for item in raw_folds if isinstance(item, Mapping)) if isinstance(raw_folds, list) else ()
    valid_count = aggregate.get("valid_folds_information_ratio")
    return FoldEvidence(
        fold_count=(int(report["num_folds"]) if isinstance(report.get("num_folds"), int) else None),
        valid_fold_count=(int(valid_count) if isinstance(valid_count, int) else None),
        mean_information_ratio=_finite_float(aggregate.get("mean_information_ratio")),
        std_information_ratio=_finite_float(aggregate.get("std_information_ratio")),
        worst_drawdown=_finite_float(aggregate.get("worst_drawdown")),
        test_window_coverage=_mapping(report.get("test_window_coverage")),
        folds=folds,
    )


def build_comparison_run(
    *,
    run_id: str,
    engine: str,
    status: str,
    created_at: str,
    config_path: str,
    report_path: str,
    log_paths: Iterable[str],
    config: Mapping[str, Any],
    report: Mapping[str, Any],
    issues: Iterable[ComparisonIssue] = (),
) -> ComparisonRun:
    """Transform already-read artifacts into one displayable research run."""
    collected_issues = list(issues)
    if engine not in {"pipeline", "walk_forward"}:
        collected_issues.append(ComparisonIssue("unsupported_engine", f"不支持的研究运行类型：{engine or '未记录'}。"))
        contract: dict[str, str | None] = {key: None for key, _ in CONTRACT_FIELDS}
        metrics: tuple[ReportMetric, ...] = ()
        fold_evidence = None
    elif engine == "pipeline":
        contract = _pipeline_contract(report)
        metrics = _pipeline_metrics(report)
        fold_evidence = None
    else:
        contract = _walk_forward_contract(report, config)
        metrics = _walk_forward_metrics(report)
        fold_evidence = _walk_forward_evidence(report)

    for key, label in CONTRACT_FIELDS:
        if not contract.get(key):
            collected_issues.append(ComparisonIssue("missing_contract", f"{label}未在现有工件中完整记录。"))

    if not report:
        collected_issues.append(ComparisonIssue("missing_report", "报告工件不可用，无法核验现有指标。"))

    model_source = _mapping(report.get("config")) or config
    data_provenance_source = (
        "pipeline_report.json:backtest.provenance.config.runtime"
        if engine == "pipeline" and contract.get("data_provenance")
        else "config.yaml: provider_uri / region / adjust_mode"
        if engine == "walk_forward" and contract.get("data_provenance")
        else None
    )
    return ComparisonRun(
        run_id=run_id,
        engine=engine,
        status=status,
        created_at=created_at,
        config_path=config_path,
        report_path=report_path,
        log_paths=tuple(log_paths),
        contract=contract,
        metrics=metrics,
        metric_status=_effective_metric_status(report),
        model_identity=_stable_value(model_source.get("model_type")),
        config_identity=_stable_value(_nested(report, "backtest", "provenance", "config_fingerprint")),
        # Version is deliberately unavailable until a producer writes one.  A
        # provider URI is provenance, not a package-version claim.
        data_package_version=None,
        data_provenance_source=data_provenance_source,
        fold_evidence=fold_evidence,
        issues=tuple(collected_issues),
    )


def information_ratio(run: ComparisonRun) -> float | None:
    """Return the existing IR metric for controlled ordering, if present."""
    for metric in run.metrics:
        if "信息比率" in metric.label:
            return metric.value
    return None


def assess_comparability(runs: Iterable[ComparisonRun]) -> ComparabilityResult:
    """Block any incomplete/mismatched selection before assigning ranks."""
    selected = tuple(runs)
    reasons: list[str] = []
    if not 2 <= len(selected) <= 5:
        reasons.append("请选择 2–5 个研究运行后再进行对比。")

    for run in selected:
        for issue in run.issues:
            reasons.append(f"{run.run_id}：{issue.message}")

    for key, label in CONTRACT_FIELDS:
        values = {run.contract.get(key) for run in selected}
        if None in values or "" in values:
            reasons.append(f"{label}存在未记录值，无法确认可比性。")
        elif len(values) > 1:
            reasons.append(f"{label}不一致：" + "；".join(
                f"{run.run_id}={run.contract.get(key)}" for run in selected
            ))

    for run in selected:
        if run.metric_status != OFFICIAL_METRIC_STATUS:
            status = run.metric_status or "未标注"
            reasons.append(
                f"{run.run_id}：指标状态为 {status}，不可作为可排序的正式指标证据。"
            )
        if information_ratio(run) is None:
            reasons.append(f"{run.run_id}：现有报告未提供可排序的信息比率。")

    if reasons:
        return ComparabilityResult(False, tuple(dict.fromkeys(reasons)), ())
    ranked = tuple(
        run.run_id for run in sorted(
            selected,
            key=lambda item: (-float(information_ratio(item) or 0.0), item.run_id),
        )
    )
    return ComparabilityResult(True, (), ranked)


def parse_selected_run_ids(raw: str) -> tuple[str, ...]:
    """Split a URL value already validated by ``_param_guard``."""
    return tuple(part for part in raw.split(",") if part)


def selectable_catalog_rows(rows: Iterable[JobSummary]) -> tuple[JobSummary, ...]:
    """Return one inspectable, current catalog row for each artifact directory."""
    selected: list[JobSummary] = []
    seen_run_ids: set[str] = set()
    for job in fold_catalog_by_dir(rows).newest:
        if (
            job.type not in {"pipeline", "walk_forward"}
            or not job.run_id
            or job.run_id in seen_run_ids
        ):
            continue
        seen_run_ids.add(job.run_id)
        selected.append(job)
    return tuple(selected)


__all__ = [
    "CONTRACT_FIELDS",
    "ComparabilityResult",
    "ComparisonIssue",
    "ComparisonRun",
    "FoldEvidence",
    "ReportMetric",
    "assess_comparability",
    "build_comparison_run",
    "information_ratio",
    "parse_selected_run_ids",
    "selectable_catalog_rows",
]
