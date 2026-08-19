"""Read-only research workbench for comparing existing run artifacts."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import streamlit as st
import yaml

from web.operator_ui import artifact_reader
from web.operator_ui._param_guard import sanitize
from web.operator_ui._path_guard import guard_output_path
from web.operator_ui.job_io import JobSummary, anchored_run_dir, load_all_jobs_read_only
from web.operator_ui.page_header import render_page_header
from web.operator_ui.pages._research_run_comparison_helpers import (
    CONTRACT_FIELDS,
    ComparisonIssue,
    ComparisonRun,
    assess_comparability,
    build_comparison_run,
    parse_selected_run_ids,
)

_LOG_NAMES = ("stdout.log", "stderr.log", "runner_stdout.log", "runner_stderr.log")


def _artifact_issue(prefix: str, issue: object) -> ComparisonIssue:
    detail = getattr(issue, "message", "无法读取工件。")
    return ComparisonIssue("artifact_read", f"{prefix}：{detail}")


def _read_config(path: Path, issues: list[ComparisonIssue]) -> Mapping[str, object]:
    result = artifact_reader.read_bytes_artifact(path, artifact_name="config.yaml")
    if result.issue is not None:
        issues.append(_artifact_issue("config.yaml 需核验", result.issue))
        return {}
    if not path.is_file():
        issues.append(ComparisonIssue("missing_config", "config.yaml 缺失，无法核验研究合同。"))
        return {}
    if not result.value:
        issues.append(ComparisonIssue("invalid_config", "config.yaml 为空，无法核验研究合同。"))
        return {}
    try:
        loaded = yaml.safe_load(bytes(result.value).decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        issues.append(ComparisonIssue("invalid_config", f"config.yaml 需核验：{type(exc).__name__}: {exc}"))
        return {}
    if not isinstance(loaded, Mapping):
        issues.append(ComparisonIssue("invalid_config", "config.yaml 顶层必须是映射，当前无法核验。"))
        return {}
    return loaded


def _read_report(path: Path, issues: list[ComparisonIssue]) -> Mapping[str, object]:
    result = artifact_reader.read_json_artifact(path, artifact_name=path.name)
    if result.issue is not None:
        issues.append(_artifact_issue(f"{path.name} 需核验", result.issue))
        return {}
    if not path.is_file():
        issues.append(ComparisonIssue("missing_report", f"{path.name} 缺失，无法读取现有指标。"))
        return {}
    return result.value if isinstance(result.value, Mapping) else {}


def _load_comparison_run(job: JobSummary) -> ComparisonRun:
    """Read one selected run through the standard output-path guard."""
    issues: list[ComparisonIssue] = []
    engine = job.type
    if engine not in {"pipeline", "walk_forward"}:
        return build_comparison_run(
            run_id=job.run_id,
            engine=engine,
            status=job.status,
            created_at=job.created_at or job.finished_at,
            config_path="",
            report_path="",
            log_paths=(),
            config={},
            report={},
            issues=(ComparisonIssue("unsupported_engine", "作业目录记录的研究运行类型不受支持。"),),
        )

    if not job.run_dir:
        return build_comparison_run(
            run_id=job.run_id,
            engine=engine,
            status=job.status,
            created_at=job.created_at or job.finished_at,
            config_path="",
            report_path="",
            log_paths=(),
            config={},
            report={},
            issues=(ComparisonIssue("missing_run_dir", "作业目录未记录产物路径，需核验。"),),
        )

    run_dir = anchored_run_dir(job.run_dir)
    try:
        guard_output_path(run_dir)
    except ValueError as exc:
        return build_comparison_run(
            run_id=job.run_id,
            engine=engine,
            status=job.status,
            created_at=job.created_at or job.finished_at,
            config_path="",
            report_path="",
            log_paths=(),
            config={},
            report={},
            issues=(ComparisonIssue("unsafe_run_dir", f"运行目录在可读边界外：{exc}"),),
        )

    config_path = run_dir / "config.yaml"
    report_path = run_dir / (
        "pipeline_report.json" if engine == "pipeline" else "walk_forward_report.json"
    )
    config = _read_config(config_path, issues)
    report = _read_report(report_path, issues)
    log_paths = tuple(str(run_dir / name) for name in _LOG_NAMES if (run_dir / name).is_file())
    return build_comparison_run(
        run_id=job.run_id,
        engine=engine,
        status=job.status,
        created_at=job.created_at or job.finished_at,
        config_path=str(config_path),
        report_path=str(report_path),
        log_paths=log_paths,
        config=config,
        report=report,
        issues=issues,
    )


def _selectable_runs() -> tuple[JobSummary, ...]:
    """Return unique catalog identities; artifact validation happens on select."""
    seen: set[str] = set()
    result: list[JobSummary] = []
    for job in load_all_jobs_read_only():
        if job.type not in {"pipeline", "walk_forward"} or not job.run_id:
            continue
        if job.run_id in seen:
            continue
        seen.add(job.run_id)
        result.append(job)
    return tuple(result)


def _metric_value(run: ComparisonRun, fragment: str) -> float | None:
    return next((metric.value for metric in run.metrics if fragment in metric.label), None)


def _run_label(job: JobSummary) -> str:
    timestamp = job.created_at or job.finished_at or "时间未记录"
    return f"{job.run_id} · {job.type} · {job.status} · {timestamp}"


render_page_header("研究运行对比", "先核验实验合同，再阅读历史研究结果。")
st.caption("只读研究工作台：不启动作业、不重算指标、不构成生产或交易建议。")

try:
    catalog = _selectable_runs()
except (OSError, RuntimeError, ValueError) as exc:
    st.error(f"无法读取统一作业目录：{type(exc).__name__}: {exc}")
    st.stop()

if not catalog:
    st.info("暂无可用于研究对比的流水线或滚动验证运行。")
    st.stop()

by_id = {job.run_id: job for job in catalog}
requested = parse_selected_run_ids(sanitize("run_ids", st.query_params.get("run_ids", "")))
unknown_requested = tuple(run_id for run_id in requested if run_id not in by_id)
default_ids = [run_id for run_id in requested if run_id in by_id]
if unknown_requested:
    st.warning("以下 URL 运行 ID 当前不在可选目录中，未加载：" + "、".join(unknown_requested))

selected_ids = st.multiselect(
    "选择 2–5 个历史研究运行",
    options=list(by_id),
    default=default_ids,
    max_selections=5,
    format_func=lambda run_id: _run_label(by_id[run_id]),
    help="选择会写入 URL，便于复查同一组历史运行。",
)
if tuple(selected_ids) != requested:
    if selected_ids:
        st.query_params["run_ids"] = ",".join(selected_ids)
    elif "run_ids" in st.query_params:
        del st.query_params["run_ids"]

if not 2 <= len(selected_ids) <= 5:
    st.info("请选择 2–5 个运行。达到数量后，页面会只读加载其既有工件并核验可比性。")
    st.stop()

runs = tuple(_load_comparison_run(by_id[run_id]) for run_id in selected_ids)
decision = assess_comparability(runs)

if decision.eligible:
    st.success("实验合同一致且指标完整：可按既有信息比率进行受控的研究排序。")
    rank_map = {run_id: index + 1 for index, run_id in enumerate(decision.ranked_run_ids)}
else:
    st.warning("当前选择不可直接排名；以下是需要核验或保持差异的原因。")
    for reason in decision.reasons:
        st.markdown(f"- {reason}")
    rank_map: dict[str, int] = {}

st.subheader("实验合同核验")
contract_rows: list[dict[str, str]] = []
for key, label in CONTRACT_FIELDS:
    row = {"字段": label}
    for run in runs:
        row[run.run_id] = run.contract.get(key) or "N/A（未记录，需核验）"
    contract_rows.append(row)
st.dataframe(contract_rows, use_container_width=True, hide_index=True)

st.subheader("既有指标与研究排序")
metric_rows: list[dict[str, object]] = []
for run in runs:
    metric_rows.append({
        "研究排序（仅可比时）": rank_map.get(run.run_id, "—"),
        "运行 ID": run.run_id,
        "类型": run.engine,
        "指标状态": run.metric_status or "N/A",
        "信息比率（扣费后超额）": _metric_value(run, "信息比率"),
        "年化超额（扣费后）": _metric_value(run, "年化超额"),
        "回撤（扣费后超额）": _metric_value(run, "回撤"),
        "模型": run.model_identity or "N/A",
        "配置指纹": run.config_identity or "N/A（现有工件未记录）",
        "数据包版本": run.data_package_version or "N/A（现有工件未记录）",
        "数据来源证据": run.data_provenance_source or "N/A（未能核验）",
    })
st.dataframe(metric_rows, use_container_width=True, hide_index=True)
st.caption("信息比率、年化超额和回撤均直接读取各报告已写入的扣费后超额口径；页面不做重算。")

walk_forward_runs = [run for run in runs if run.fold_evidence is not None]
if walk_forward_runs:
    st.subheader("滚动验证稳定性证据")
    fold_rows: list[dict[str, object]] = []
    for run in walk_forward_runs:
        evidence = run.fold_evidence
        assert evidence is not None
        fold_rows.append({
            "运行 ID": run.run_id,
            "折数": evidence.fold_count,
            "有效 IR 折数": evidence.valid_fold_count,
            "平均 IR": evidence.mean_information_ratio,
            "IR 标准差": evidence.std_information_ratio,
            "最差超额回撤": evidence.worst_drawdown,
            "测试覆盖": str(dict(evidence.test_window_coverage)) or "N/A",
        })
    st.dataframe(fold_rows, use_container_width=True, hide_index=True)
    with st.expander("查看每折既有证据", expanded=False):
        for run in walk_forward_runs:
            evidence = run.fold_evidence
            assert evidence is not None
            st.markdown(f"**{run.run_id}**")
            st.dataframe(list(evidence.folds), use_container_width=True, hide_index=True)

st.subheader("精确追溯")
for run in runs:
    with st.expander(f"{run.run_id} · {run.engine}", expanded=False):
        result_col, wf_col = st.columns(2)
        result_col.page_link(
            "pages/results.py",
            label="打开结果（含配置与日志）",
            query_params={"run_id": run.run_id},
        )
        if run.engine == "walk_forward":
            wf_col.page_link(
                "pages/walk_forward.py",
                label="打开滚动验证",
                query_params={"run_id": run.run_id},
            )
        else:
            wf_col.caption("流水线运行不适用滚动验证详情。")
        st.caption("以下是只读工件引用；对比页不会修改配置或日志。")
        st.code(run.config_path or "N/A（未记录）", language=None)
        st.code(run.report_path or "N/A（未记录）", language=None)
        if run.log_paths:
            st.code("\n".join(run.log_paths), language=None)
        else:
            st.caption("未发现既有日志文件。")
        if run.issues:
            for issue in run.issues:
                st.warning(f"需核验：{issue.message}")
