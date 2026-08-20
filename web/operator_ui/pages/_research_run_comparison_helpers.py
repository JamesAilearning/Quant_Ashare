"""Read models and pure comparison gates for the research comparison page.

This module intentionally does not import Streamlit or read files.  Artifact
loading belongs to the page boundary; the transformation below consumes only
already-read YAML/JSON mappings, which keeps the conservative comparison gate
easy to test and prevents this view from becoming another metrics engine.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from src.core.canonical_backtest_contract import (
    CANONICAL_OFFICIAL_BACKTEST_PATH,
    COMMISSION_RATE_MAX,
    OFFICIAL_METRIC_STATUS,
    SLIPPAGE_BPS_MAX,
    STAMP_TAX_BPS_MAX,
    SUPPORTED_ADJUST_MODES,
    SUPPORTED_EXECUTION_PRICE_KINDS,
)
from src.core.backtest_runner import (
    EXECUTION_TIMING_SEMANTICS,
    PRICE_LIMIT_SEMANTICS,
)
from web.operator_ui.job_io import (
    JobSummary,
    anchored_run_dir,
    canonical_dir_key,
    fold_catalog_by_dir,
)

CONTRACT_FIELDS: tuple[tuple[str, str], ...] = (
    ("engine", "研究运行类型"),
    ("universe", "股票池 / universe"),
    ("training_window", "训练窗口"),
    ("validation_window", "验证窗口"),
    ("testing_window", "测试窗口"),
    ("benchmark", "回测基准"),
    ("execution_lag", "信号至成交滞后"),
    ("execution_semantics_provenance", "执行时序与涨跌停语义指纹"),
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


@dataclass(frozen=True)
class SelectableCatalog:
    """Current selectable rows plus same-run CLI-to-UI ID aliases."""

    rows: tuple[JobSummary, ...]
    run_id_alias: Mapping[str, str]


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
        try:
            number = float(value)
        except OverflowError:
            return None
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


def _required_text(value: Any) -> str | None:
    """Accept the non-empty string fields written by both run producers."""
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _date_window_bounds(value: Any) -> tuple[date, date] | None:
    """Return a strict producer date window without accepting overlap inputs."""
    text = _required_text(value)
    if text is None:
        return None
    parts = [part.strip() for part in text.split("~", maxsplit=1)]
    if len(parts) != 2:
        return None
    try:
        start, end = (date.fromisoformat(part) for part in parts)
    except ValueError:
        return None
    if start >= end:
        return None
    return start, end


def _date_window(value: Any) -> str | None:
    """Validate the producer's ``YYYY-MM-DD ~ YYYY-MM-DD`` period format."""
    bounds = _date_window_bounds(value)
    if bounds is None:
        return None
    start, end = bounds
    return f"{start.isoformat()} ~ {end.isoformat()}"


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return int(value)


def _non_boolean_int(value: Any) -> int | None:
    """Return producer-recorded count evidence without accepting ``bool``."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return int(value)


def _window_from_pipeline(config: Mapping[str, Any], key: str) -> str | None:
    return _date_window(config.get(key))


def _pipeline_windows_are_ordered(config: Mapping[str, Any]) -> bool:
    """Match the canonical pipeline's strict train/valid/test ordering."""
    train = _date_window_bounds(config.get("train_period"))
    valid = _date_window_bounds(config.get("valid_period"))
    test = _date_window_bounds(config.get("test_period"))
    if train is None or valid is None or test is None:
        return False
    return train[1] < valid[0] and valid[1] < test[0]


def _config_has_explicit_provider(config: Mapping[str, Any]) -> bool:
    """Require the producer-owned runtime key from an artifact config.yaml."""
    return _required_text(config.get("provider_uri")) is not None


def _window_from_walk_forward(config: Mapping[str, Any], kind: str) -> str | None:
    start = _required_text(config.get("overall_start"))
    end = _required_text(config.get("overall_end"))
    months = _positive_int(config.get(f"{kind}_months"))
    if not (start and end and months):
        return None
    period = _date_window(f"{start} ~ {end}")
    if period is None:
        return None
    return f"{period}; {kind}_months={months}"


def _test_window_coverage(config: Mapping[str, Any], value: Any) -> str | None:
    """Bind producer-recorded walk-forward schedule and coverage evidence."""
    test_months = _positive_int(config.get("test_months"))
    step_months = _positive_int(config.get("step_months"))
    if test_months is None or step_months is None:
        return None
    coverage = _mapping(value)
    numeric_keys = (
        "gap_count",
        "max_gap_days",
        "overlap_count",
        "max_overlap_days",
        "max_overlap_depth",
    )
    if coverage.get("mode") not in {"none", "continuous", "gapped", "overlapping", "mixed"}:
        return None
    if any(
        isinstance(coverage.get(key), bool)
        or not isinstance(coverage.get(key), int)
        or coverage[key] < 0
        for key in numeric_keys
    ):
        return None
    return json.dumps(
        {
            "test_months": test_months,
            "step_months": step_months,
            "coverage": {
                "mode": coverage["mode"],
                **{key: coverage[key] for key in numeric_keys},
            },
        },
        sort_keys=True,
        ensure_ascii=False,
    )


def _bounded_number(value: Any, *, lower: float, upper: float | None = None) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    if not math.isfinite(number) or number < lower or (upper is not None and number > upper):
        return None
    return number


def _stamp_tax_schedule_identity(value: Any) -> list[dict[str, float | str]] | str | None:
    """Validate the serialized canonical stamp-tax schedule before comparing it."""
    if value is None:
        # ``None`` is the explicit producer-configured request for the
        # canonical CN default.  It is distinguishable from an absent key at
        # the caller and must not be silently treated as missing evidence.
        return "canonical_default"
    if not isinstance(value, (list, tuple)) or not value:
        return None
    entries: list[dict[str, float | str]] = []
    previous: date | None = None
    for item in value:
        if not isinstance(item, Mapping):
            return None
        effective_from = _required_text(item.get("effective_from"))
        bps = _bounded_number(item.get("bps"), lower=0.0, upper=STAMP_TAX_BPS_MAX)
        if effective_from is None or bps is None:
            return None
        try:
            schedule_date = date.fromisoformat(effective_from)
        except ValueError:
            return None
        if previous is not None and schedule_date <= previous:
            return None
        previous = schedule_date
        entries.append({"effective_from": schedule_date.isoformat(), "bps": bps})
    return entries


def _exchange_cost_identity(
    *,
    execution_price_kind: Any,
    commission_rate: Any,
    stamp_tax_schedule: Any,
    slippage_bps: Any,
    min_cost: Any,
    init_cash: Any,
    limit_threshold: Any,
    adjust_mode: Any,
) -> str | None:
    """Validate only producer-compatible canonical exchange/cost controls."""
    execution = _required_text(execution_price_kind)
    adjust = _required_text(adjust_mode)
    commission = _bounded_number(commission_rate, lower=0.0, upper=COMMISSION_RATE_MAX)
    slippage = _bounded_number(slippage_bps, lower=0.0, upper=SLIPPAGE_BPS_MAX)
    minimum = _bounded_number(min_cost, lower=0.0)
    initial_cash = _bounded_number(init_cash, lower=0.0)
    limit = _bounded_number(limit_threshold, lower=0.0, upper=0.25)
    schedule = _stamp_tax_schedule_identity(stamp_tax_schedule)
    if (
        execution not in SUPPORTED_EXECUTION_PRICE_KINDS
        or adjust not in SUPPORTED_ADJUST_MODES
        or commission is None
        or slippage is None
        or minimum is None
        or initial_cash is None
        or initial_cash == 0.0
        or limit is None
        or limit == 0.0
        or schedule is None
    ):
        return None
    return json.dumps(
        {
            "execution_price_kind": execution,
            "commission_rate": commission,
            "stamp_tax_schedule": schedule,
            "slippage_bps": slippage,
            "min_cost": minimum,
            "init_cash": initial_cash,
            "limit_threshold": limit,
            "adjust_mode": adjust,
        },
        sort_keys=True,
        ensure_ascii=False,
    )


def _exchange_cost_from_request(request: Mapping[str, Any]) -> str | None:
    exchange = _mapping(request.get("exchange_config"))
    cost = _mapping(exchange.get("cost_model"))
    account = _mapping(request.get("account_config"))
    if (
        any(
            key not in exchange
            for key in ("execution_price_kind", "limit_threshold", "cost_model")
        )
        or any(
            key not in cost
            for key in ("commission_rate", "stamp_tax_schedule", "slippage_bps", "min_cost")
        )
        or "adjust_mode" not in request
        or "init_cash" not in account
    ):
        return None
    return _exchange_cost_identity(
        execution_price_kind=exchange.get("execution_price_kind"),
        commission_rate=cost.get("commission_rate"),
        stamp_tax_schedule=cost.get("stamp_tax_schedule"),
        slippage_bps=cost.get("slippage_bps"),
        min_cost=cost.get("min_cost"),
        init_cash=account.get("init_cash"),
        limit_threshold=exchange.get("limit_threshold"),
        adjust_mode=request.get("adjust_mode"),
    )


def _exchange_cost_from_config(config: Mapping[str, Any]) -> str | None:
    keys = (
        "execution_price_kind",
        "commission_rate",
        "stamp_tax_schedule",
        "slippage_bps",
        "min_cost",
        "init_cash",
        "limit_threshold",
        "adjust_mode",
    )
    if any(key not in config for key in keys):
        return None
    return _exchange_cost_identity(
        execution_price_kind=config.get("execution_price_kind"),
        commission_rate=config.get("commission_rate"),
        stamp_tax_schedule=config.get("stamp_tax_schedule"),
        slippage_bps=config.get("slippage_bps"),
        min_cost=config.get("min_cost"),
        init_cash=config.get("init_cash"),
        limit_threshold=config.get("limit_threshold"),
        adjust_mode=config.get("adjust_mode"),
    )


def _data_provenance(runtime: Mapping[str, Any]) -> str | None:
    """Return a producer-recorded runtime snapshot, never a YAML substitute."""
    if not runtime:
        return None
    required = ("provider_uri", "region", "data_adjust_mode")
    values = {key: _required_text(runtime.get(key)) for key in required}
    if any(value is None for value in values.values()):
        return None
    if values["region"] not in {"cn", "us"}:
        return None
    if values["data_adjust_mode"] not in SUPPORTED_ADJUST_MODES:
        return None
    return json.dumps(values, sort_keys=True, ensure_ascii=False)


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
    if len(normalized) != 16 or any(char not in "0123456789abcdef" for char in normalized):
        return None
    return normalized


def _execution_semantics_provenance(provenance: Mapping[str, Any]) -> str | None:
    """Return the explicit version labels written by the canonical backtest."""
    execution = _required_text(provenance.get("execution_timing_semantics"))
    price_limit = _required_text(provenance.get("price_limit_semantics"))
    if (
        execution is None
        or price_limit is None
        or execution != EXECUTION_TIMING_SEMANTICS
        or price_limit != PRICE_LIMIT_SEMANTICS
    ):
        return None
    return json.dumps(
        {
            "execution_timing_semantics": execution,
            "price_limit_semantics": price_limit,
        },
        sort_keys=True,
        ensure_ascii=False,
    )


def _pipeline_contract(report: Mapping[str, Any]) -> dict[str, str | None]:
    report_config = _mapping(report.get("config"))
    backtest_provenance = _mapping(_nested(report, "backtest", "provenance"))
    provenance = _mapping(backtest_provenance.get("config"))
    runtime = _mapping(provenance.get("runtime"))
    training_window = _window_from_pipeline(report_config, "train_period")
    validation_window = _window_from_pipeline(report_config, "valid_period")
    testing_window = _window_from_pipeline(report_config, "test_period")
    if not _pipeline_windows_are_ordered(report_config):
        training_window = validation_window = testing_window = None
    return {
        "engine": "pipeline",
        "universe": _required_text(report_config.get("instruments")),
        "training_window": training_window,
        "validation_window": validation_window,
        "testing_window": testing_window,
        "benchmark": _required_text(provenance.get("benchmark_code")),
        "execution_lag": _execution_lag(provenance.get("signal_to_execution_lag")),
        "execution_semantics_provenance": _execution_semantics_provenance(
            backtest_provenance
        ),
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
        "universe": _required_text(source.get("instruments")),
        "training_window": _window_from_walk_forward(source, "train"),
        "validation_window": _window_from_walk_forward(source, "valid"),
        "testing_window": _test_window_coverage(source, report.get("test_window_coverage")),
        "benchmark": _required_text(source.get("benchmark_code")),
        "execution_lag": _execution_lag(source.get("signal_to_execution_lag")),
        # Current aggregate artifacts do not record a semantics-bound runtime
        # fingerprint.  Do not infer it from config.yaml; ordering remains
        # unavailable until a producer writes the evidence.
        "execution_semantics_provenance": None,
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


def _walk_forward_evidence(report: Mapping[str, Any]) -> FoldEvidence | None:
    aggregate = _mapping(report.get("aggregate_metrics"))
    raw_folds = report.get("folds")
    if not isinstance(raw_folds, list) or any(
        not isinstance(item, Mapping) for item in raw_folds
    ):
        return None
    folds = tuple(raw_folds)
    valid_count = aggregate.get("valid_folds_information_ratio")
    return FoldEvidence(
        fold_count=_non_boolean_int(report.get("num_folds")),
        valid_fold_count=_non_boolean_int(valid_count),
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
        if fold_evidence is None:
            collected_issues.append(
                ComparisonIssue(
                    "invalid_fold_evidence",
                    "逐折稳定性证据结构无效，无法按既有工件展示。",
                )
            )

    for key, label in CONTRACT_FIELDS:
        if not contract.get(key):
            collected_issues.append(ComparisonIssue("missing_contract", f"{label}未在现有工件中完整记录。"))

    if not report:
        collected_issues.append(ComparisonIssue("missing_report", "报告工件不可用，无法核验现有指标。"))

    metric_status = _effective_metric_status(report)
    if (
        engine == "pipeline"
        and metric_status == OFFICIAL_METRIC_STATUS
        and _required_text(report.get("official_backtest_path"))
        != CANONICAL_OFFICIAL_BACKTEST_PATH
    ):
        collected_issues.append(
            ComparisonIssue(
                "unverified_metric_path",
                "正式指标未记录为 canonical qlib 回测路径，无法作为可排序证据。",
            )
        )

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
        metric_status=metric_status,
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


def duplicate_run_ids(run_ids: Iterable[str]) -> tuple[str, ...]:
    """Return repeated canonical IDs in first-repeat order for URL blocking."""
    seen: set[str] = set()
    duplicates: list[str] = []
    for run_id in run_ids:
        if run_id in seen and run_id not in duplicates:
            duplicates.append(run_id)
        seen.add(run_id)
    return tuple(duplicates)


def _catalog_dir_key(run_dir: str) -> str:
    """Use the same canonical directory key as the results views."""
    return canonical_dir_key(run_dir) or os.path.normcase(
        str(anchored_run_dir(run_dir))
    )


def _parse_recorded_instant(value: str) -> datetime | None:
    """Read the timezone-aware ISO timestamps written by job/catalog producers."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _row_recency(row: JobSummary) -> datetime:
    """Use recorded completion first; absent/malformed time cannot win ownership."""
    for value in (row.finished_at, row.created_at, row.started_at):
        parsed = _parse_recorded_instant(value)
        if parsed is not None:
            return parsed
    return datetime.min.replace(tzinfo=timezone.utc)


def _ui_cli_share_execution(ui_job: JobSummary, cli_job: JobSummary) -> bool:
    """Prove a CLI row was emitted during this UI job's own execution window."""
    if (
        ui_job.source != "ui"
        or cli_job.source != "cli"
        or ui_job.type != cli_job.type
        or _catalog_dir_key(ui_job.run_dir) != _catalog_dir_key(cli_job.run_dir)
    ):
        return False
    ui_started = _parse_recorded_instant(ui_job.started_at)
    ui_finished = _parse_recorded_instant(ui_job.finished_at)
    cli_started = _parse_recorded_instant(cli_job.started_at)
    cli_finished = _parse_recorded_instant(cli_job.finished_at or cli_job.created_at)
    return (
        ui_started is not None
        and ui_finished is not None
        and cli_started is not None
        and cli_finished is not None
        and ui_started <= cli_started <= cli_finished <= ui_finished
    )


def selectable_catalog(rows: Iterable[JobSummary]) -> SelectableCatalog:
    """Return one current owner per artifact dir and only proven UI/CLI aliases.

    A shared directory alone is not a mirror relationship: an operator can
    later point an independent CLI run at a former UI output directory.  A UI
    row owns a CLI row only when the catalog's complete execution interval is
    recorded inside that UI job's lifecycle.  Otherwise the newest record owns
    the current artifacts, preserving exact-run traceability.
    """
    allowed_types = {"pipeline", "walk_forward"}
    relevant = tuple(
        row
        for row in rows
        if row.type in allowed_types and row.run_id and row.run_dir
    )
    grouped: dict[tuple[str, str], list[JobSummary]] = {}
    for job in relevant:
        key = (job.type, _catalog_dir_key(job.run_dir))
        grouped.setdefault(key, []).append(job)

    selected: list[JobSummary] = []
    aliases: dict[str, str] = {}
    for group in grouped.values():
        ui_rows = [job for job in group if job.source == "ui"]
        cli_rows = sorted(
            (job for job in group if job.source == "cli"),
            key=_row_recency,
            reverse=True,
        )
        current_cli = next(iter(fold_catalog_by_dir(cli_rows).newest), None)
        mirrored_ui = (
            [job for job in ui_rows if _ui_cli_share_execution(job, current_cli)]
            if current_cli is not None
            else []
        )
        if mirrored_ui and current_cli is not None:
            owner = max(mirrored_ui, key=_row_recency)
            aliases[current_cli.run_id] = owner.run_id
        else:
            candidates = [*ui_rows, *([current_cli] if current_cli is not None else [])]
            owner = max(candidates, key=_row_recency)
        selected.append(owner)

    return SelectableCatalog(
        rows=tuple(sorted(selected, key=_row_recency, reverse=True)),
        run_id_alias=aliases,
    )


def selectable_catalog_rows(rows: Iterable[JobSummary]) -> tuple[JobSummary, ...]:
    """Return one inspectable, current catalog row for each artifact directory."""
    return selectable_catalog(rows).rows


__all__ = [
    "CONTRACT_FIELDS",
    "ComparabilityResult",
    "ComparisonIssue",
    "ComparisonRun",
    "FoldEvidence",
    "ReportMetric",
    "SelectableCatalog",
    "assess_comparability",
    "build_comparison_run",
    "duplicate_run_ids",
    "information_ratio",
    "parse_selected_run_ids",
    "selectable_catalog",
    "selectable_catalog_rows",
]
