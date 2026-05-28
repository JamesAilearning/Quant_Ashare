"""Walk-Forward page — fold-by-fold results, stability analysis, and OOS NAV.

TICKET-B contract (Option B, confirmed by the operator on 2026-05-22):

The page reads the canonical walk-forward artifacts produced by
``src.core.walk_forward.engine`` — ``walk_forward_report.json`` plus
per-fold ``fold_NN_report.json`` files (see PR #108 for the contract).

The original TICKET-B draft asked for an additional ``stitched_nav.parquet``
and a ``folds/fold_N/`` directory layout. We chose not to introduce a new
artifact contract: the existing per-fold JSONs already carry annualised
return + test windows, which is enough to **synthesise** a stitched OOS NAV
on the UI side (``_synthesised_stitched_nav``). The synthesis ignores
intra-fold path but preserves segment endpoints and final value — the
information operators actually use for stability inspection. A true
path-faithful NAV would require the walk-forward engine to emit
``nav.parquet`` per fold; that is intentionally deferred until/unless a
concrete need surfaces.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from web.operator_ui._path_guard import output_path
from web.operator_ui.chart_reader import discover_charts
from web.operator_ui.components import (
    render_empty_state,
    render_error_state,
    render_stat_card,
)
from web.operator_ui.formatting import (
    format_number,
    format_percent,
)
from web.operator_ui.job_manager import JobManager
from web.operator_ui.page_header import render_page_header
from web.operator_ui.report_reader import (
    read_fold_reports,
    read_walk_forward_report,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MISSING = "—"

# Plotly does not resolve CSS custom properties (``var(--…)``) — passing
# them yields an unstyled chart. Mirror the convention from results.py:
# use literal CSS named colours so the trace styles work even though the
# rest of the design system runs on tokens.
PLOTLY_STRATEGY_COLOR = "royalblue"
PLOTLY_POSITIVE_COLOR = "seagreen"
PLOTLY_NEGATIVE_COLOR = "firebrick"
PLOTLY_INFO_COLOR = "steelblue"
PLOTLY_FOLD_BAND_DARK = "rgba(99, 102, 241, 0.06)"
PLOTLY_FOLD_BAND_LIGHT = "rgba(99, 102, 241, 0.02)"


def _finite_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _get_metrics(entry: dict[str, Any], *keys: str) -> float | None:
    """Walk nested dicts: entry['metrics']['annual_return'] etc."""
    cur: Any = entry
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return _finite_float(cur)


def _first_metric(entry: dict[str, Any], *paths: tuple[str, ...]) -> float | None:
    for path in paths:
        value = _get_metrics(entry, *path)
        if value is not None:
            return value
    return None


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _ratio_fraction(text: str) -> float:
    if "/" not in text:
        return 0.0
    numerator, denominator = text.split("/", maxsplit=1)
    try:
        parsed_denominator = float(denominator)
        if parsed_denominator == 0:
            return 0.0
        return float(numerator) / parsed_denominator
    except ValueError:
        return 0.0


def _stop_artifact_error(title: str, exc: Exception) -> None:
    render_error_state(
        title,
        "The selected walk-forward artifact could not be read.",
        error=f"{type(exc).__name__}: {exc}",
        on_retry="window.location.reload()",
        variant="page",
    )
    st.stop()


# ---------------------------------------------------------------------------
# Stitched OOS NAV (synthesised — TICKET-B contract option B, see PR #108).
# We do NOT have per-fold ``nav.parquet`` artifacts — the walk-forward engine
# writes only the aggregate report + per-fold metrics JSON. To draw a
# continuous OOS view we synthesise NAV: each fold's segment grows from the
# previous fold's terminal NAV at the fold's annualised return, compounded
# over the actual test-window length. This is an approximation — it ignores
# intra-fold path, but it preserves the relative shape and final value
# operators care about for stability inspection.
# ---------------------------------------------------------------------------


def _synthesised_stitched_nav(
    fold_data: list[dict[str, Any]],
) -> tuple[list[Any], list[float], list[tuple[Any, Any, int]]]:
    """Return (timeline, nav, fold_bands).

    ``timeline`` is the X-axis dates (pd.Timestamp), ``nav`` is the
    accumulated NAV starting from 1.0, and ``fold_bands`` is a list of
    ``(start, end, ordinal)`` for shading per-fold regions.

    Folds without parseable ``test_start`` / ``test_end`` or without an
    ``annual_return`` are skipped — never silently treated as zero (which
    would distort the curve). Skipped folds are surfaced to the caller
    via the empty timeline / empty bands so the UI shows an empty state.
    """

    if not fold_data:
        return [], [], []

    timeline: list[Any] = []
    nav: list[float] = []
    bands: list[tuple[Any, Any, int]] = []
    current_nav = 1.0
    for fd in fold_data:
        ts = fd.get("test_start") or ""
        te = fd.get("test_end") or ""
        ar = fd.get("annual_return")
        if not ts or not te or ar is None:
            continue
        try:
            start = pd.Timestamp(str(ts))
            end = pd.Timestamp(str(te))
        except (ValueError, TypeError):
            continue
        if end <= start:
            continue
        days = (end - start).days
        years = days / 365.0
        # Reject ``annual_return <= -1.0`` *before* exponentiation.
        # Python's ``a ** b`` returns a **complex** number when the base
        # is negative and the exponent is non-integer (rather than
        # raising ValueError / OverflowError), and Plotly then errors
        # at render time, blanking the Walk-Forward page. A return of
        # -100% or worse over a fold also has no sensible NAV
        # interpretation for a long-only synthetic stitched curve, so
        # we skip the fold rather than guess.
        base = 1.0 + float(ar)
        if base < 0.0:
            continue
        try:
            end_nav = current_nav * (base ** years)
        except (ValueError, OverflowError):
            continue
        # Defence-in-depth: still type/finiteness-check the result —
        # `base == 0` with ``years <= 0`` (degenerate test window) or
        # NumPy-imported floats with surprising semantics could slip
        # through, and we never want a complex / inf / nan to reach
        # Plotly.
        if not isinstance(end_nav, (int, float)) or not math.isfinite(end_nav):
            continue
        # Use simple linear interpolation between fold start and end so
        # adjacent folds connect visually; without this each fold would
        # look like a step function.
        timeline.append(start)
        nav.append(current_nav)
        timeline.append(end)
        nav.append(end_nav)
        bands.append((start, end, int(fd.get("ordinal") or 0)))
        current_nav = end_nav
    return timeline, nav, bands


# ---------------------------------------------------------------------------
# Logs reader (TICKET-B "Logs tab"). Reads the standard log filenames
# already used by the pipeline / walk-forward runners.
# ---------------------------------------------------------------------------
_LOG_NAMES: tuple[str, ...] = (
    "stdout.log",
    "stderr.log",
    "runner_stdout.log",
    "runner_stderr.log",
)


def _read_log_files(run_dir: Path) -> list[tuple[str, str]]:
    """Return ``(name, text)`` pairs for any log files that exist.

    Reads with ``errors='replace'`` so a partial-encoding tail does not
    crash the UI. Truncates each file to the trailing 64 KiB — the head
    is rarely useful to an operator triaging a fold and the renderer
    cost scales linearly with size.
    """

    out: list[tuple[str, str]] = []
    if not run_dir.is_dir():
        return out
    for name in _LOG_NAMES:
        candidate = run_dir / name
        if not candidate.is_file():
            continue
        try:
            data = candidate.read_bytes()
        except OSError:
            continue
        tail = data[-64 * 1024:] if len(data) > 64 * 1024 else data
        text = tail.decode("utf-8", errors="replace")
        if len(data) > 64 * 1024:
            text = "[truncated to last 64 KiB]\n" + text
        out.append((name, text))
    return out


def _compute_stability_score(
    ir_list: list[float], dd_list: list[float],
) -> tuple[float, dict[str, Any]]:
    """Compute a composite stability score (0-1) from fold metrics."""
    n = len(ir_list)
    if n < 2:
        return 0.0, {"error": "Need at least 2 folds"}

    mean_s = sum(ir_list) / n
    var_s = sum((s - mean_s) ** 2 for s in ir_list) / n
    std_s = math.sqrt(var_s)
    cv = std_s / abs(mean_s) if mean_s != 0 else 1.0
    cv_clamped = min(cv, 1.0)

    n_positive = sum(1 for s in ir_list if s > 0)
    n_above_1 = sum(1 for s in ir_list if s > 1.0)

    # DD concentration: how concentrated is the worst drawdown?
    if len(dd_list) >= 2:
        worst = min(dd_list)  # most negative
        dd_concentration = 1.0 - (abs(worst) / (abs(max(dd_list)) + 0.0001))
        dd_concentration = max(0.0, min(1.0, dd_concentration))
    else:
        dd_concentration = 0.5

    # Spearman trend: are later folds worse?
    if n >= 3:
        ranks = sorted(range(n), key=lambda i: ir_list[i])
        rank_map = {idx: rank for rank, idx in enumerate(ranks)}
        fold_ids = list(range(1, n + 1))
        fold_ranks = [rank_map[i] for i in range(n)]
        mean_fold = (n + 1) / 2
        mean_rank = (n - 1) / 2
        cov = sum((f - mean_fold) * (r - mean_rank) for f, r in zip(fold_ids, fold_ranks, strict=True)) / n
        std_f = math.sqrt(sum((f - mean_fold) ** 2 for f in fold_ids) / n)
        std_r = math.sqrt(sum((r - mean_rank) ** 2 for r in fold_ranks) / n)
        if std_f > 0 and std_r > 0:
            spearman = cov / (std_f * std_r)
        else:
            spearman = 0.0
    else:
        spearman = 0.0
    trend_stable = abs(spearman) < 0.3

    score = (
        0.4 * (1.0 - cv_clamped)
        + 0.3 * (n_positive / n)
        + 0.2 * dd_concentration
        + 0.1 * (1.0 if trend_stable else 0.0)
    )
    details = {
        "ir_cv": cv,
        "positive_folds": f"{n_positive}/{n}",
        "above_ir_1": f"{n_above_1}/{n}",
        "dd_concentration": dd_concentration,
        "spearman": spearman,
        "trend_stable": trend_stable,
    }
    return min(1.0, max(0.0, score)), details


def _stability_label(score: float) -> str:
    if score >= 0.8:
        return "高度稳定"
    if score >= 0.6:
        return "较稳定"
    if score >= 0.3:
        return "不稳定"
    return "极不稳定"


def _stability_color(score: float) -> str:
    if score >= 0.8:
        return "positive"
    if score >= 0.6:
        return "info"
    if score >= 0.3:
        return "warning"
    return "negative"


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
render_page_header("滚动验证详情", "单折结果、稳定性分析以及样本外净值。")

# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
jobs = JobManager.list_jobs()
wf_jobs = [j for j in jobs if j.get("mode") == "walk_forward" and j.get("run_dir")]
run_options = {j["run_dir"]: j.get("job_id", "?") for j in wf_jobs if j.get("run_dir")}

# Pre-seed ``selected`` so bare-mode imports (no Streamlit script
# context — ``st.stop()`` becomes a no-op) have a defined value for
# the module-level ``run_dir = Path(str(selected))`` reference below.
# Production runs overwrite this in the ``else`` branch before
# reaching that line. See test_operator_ui_walk_forward_source.
selected: str | None = str(output_path())

if not run_options:
    render_empty_state(
        "\U0001f501",
        "暂无滚动验证记录",
        "滚动验证（Walk-Forward）通过在滚动时间窗上反复训练并在样本外测试，"
        "评估策略的鲁棒性。",
    )
    if st.button("配置运行"):
        st.switch_page("pages/config_run.py")
    st.stop()
else:
    # If the operator clicked through from the Jobs hub, the selected
    # run id is in ``st.query_params["run_id"]`` (or stashed in
    # ``st.session_state["wf_selected_run"]`` as a fallback for clients
    # that strip query strings). Pre-select the matching run so the
    # detail page lands on the row the operator clicked, not the most
    # recent run.
    # Sanitize the URL-supplied run_id (rejects path traversal / shell
    # metacharacters); fall through to session_state if missing/invalid.
    # See web/operator_ui/_param_guard.py.
    from web.operator_ui._param_guard import sanitize as _sanitize_qp

    _requested_run_id = _sanitize_qp(
        "run_id", st.query_params.get("run_id", ""), default="",
    ) or str(st.session_state.get("wf_selected_run", "") or "")
    _default_index = 0
    if _requested_run_id:
        _keys = list(run_options.keys())
        for idx, key in enumerate(_keys):
            if run_options[key] == _requested_run_id:
                _default_index = idx
                break
    selected = st.selectbox(
        "运行",
        options=list(run_options.keys()),
        format_func=lambda k: run_options[k],
        index=_default_index,
    )
    if not selected:
        st.stop()

# In bare-Python (no Streamlit context), st.selectbox returns None
# which causes Path() to fail.  Always coerce to string first so the
# module is importable outside `streamlit run`.
run_dir = Path(str(selected))

# ---------------------------------------------------------------------------
# Read report (guarded for bare-Python import where selected may be None)
# ---------------------------------------------------------------------------
try:
    wf_report = read_walk_forward_report(run_dir)
except (ValueError, OSError) as exc:
    _stop_artifact_error("无法读取滚动验证报告", exc)
    wf_report = {"folds": []}
folds = wf_report.get("folds", [])

# Try to read folds from fold directories if not in report
if not folds:
    fold_reports: list[dict[str, Any]] | None
    try:
        fold_reports = read_fold_reports(run_dir)
    except (ValueError, OSError) as exc:
        _stop_artifact_error("无法读取单折报告", exc)
        fold_reports = None
    if fold_reports:
        folds = fold_reports

if not folds:
    render_empty_state(
        "\U0001f4ca",
        "暂无单折数据",
        "滚动验证作业完成后，单折报告会出现在这里。",
    )
    charts: dict[str, Path] | None
    try:
        charts = discover_charts(run_dir)
    except (ValueError, OSError) as exc:
        _stop_artifact_error("无法发现滚动验证图表", exc)
        charts = None
    if charts:
        st.header("图表")
        for _label, path in charts.items():
            st.image(str(path), use_container_width=True)
    st.stop()

aggregate_metrics = wf_report.get("aggregate_metrics")
aggregate = aggregate_metrics if isinstance(aggregate_metrics, dict) else {}

# ---------------------------------------------------------------------------
# Collect fold metrics
# ---------------------------------------------------------------------------
fold_data = []
ir_list: list[float] = []
return_list: list[float] = []
dd_list: list[float] = []
drawdown_by_fold: list[tuple[Any, float]] = []
turnover_list: list[float] = []
win_rate_list: list[float] = []

for i, fold_entry in enumerate(folds):
    fd: dict[str, Any] = {
        "index": fold_entry.get("fold_index", i + 1),
        "ordinal": i + 1,
    }

    # Direct fold entry fields from walk_forward_report.json
    fd["annual_return"] = _first_metric(fold_entry, ("annualized_return",), ("annual_return",))
    fd["information_ratio"] = _first_metric(fold_entry, ("information_ratio",))
    fd["max_drawdown"] = _get_metrics(fold_entry, "max_drawdown")
    fd["turnover"] = _first_metric(fold_entry, ("turnover_daily",), ("turnover",))
    fd["win_rate"] = _get_metrics(fold_entry, "win_rate")
    fd["n_trades"] = fold_entry.get("n_trades")

    # Also try nested metrics from fold report
    m = fold_entry.get("metrics") if isinstance(fold_entry.get("metrics"), dict) else {}
    if m:
        if fd["annual_return"] is None:
            fd["annual_return"] = _first_metric(m, ("annualized_return",), ("annual_return",))
        if fd["information_ratio"] is None:
            fd["information_ratio"] = _first_metric(m, ("information_ratio",))
        if fd["max_drawdown"] is None:
            fd["max_drawdown"] = _get_metrics(m, "max_drawdown")
        if fd["turnover"] is None:
            fd["turnover"] = _first_metric(m, ("turnover_daily",), ("turnover",))
        if fd["win_rate"] is None:
            fd["win_rate"] = _get_metrics(m, "win_rate")
        if fd["n_trades"] is None:
            fd["n_trades"] = m.get("n_trades")

    # Train/test period labels
    fd["train_period"] = fold_entry.get("train_period", "")
    fd["test_period"] = fold_entry.get("test_period", "")
    fd["train_start"] = fold_entry.get("train_start", "")
    fd["test_start"] = fold_entry.get("test_start", "")
    fd["test_end"] = fold_entry.get("test_end", "")
    period = str(fd["test_period"] or "")
    if fd["test_start"] and fd["test_end"]:
        period = f"{str(fd['test_start'])[:7]} → {str(fd['test_end'])[:7]}"
    fd["period"] = period

    fold_data.append(fd)
    if fd["information_ratio"] is not None:
        ir_list.append(fd["information_ratio"])
    if fd["annual_return"] is not None:
        return_list.append(fd["annual_return"])
    if fd["max_drawdown"] is not None:
        dd_list.append(fd["max_drawdown"])
        drawdown_by_fold.append((fd["index"], fd["max_drawdown"]))
    if fd["turnover"] is not None:
        turnover_list.append(fd["turnover"])
    if fd["win_rate"] is not None:
        win_rate_list.append(fd["win_rate"])

# ---------------------------------------------------------------------------
# Stability score
# ---------------------------------------------------------------------------
if ir_list and dd_list:
    score, score_details = _compute_stability_score(ir_list, dd_list)
else:
    score = -1.0
    score_details = {}

n_folds = len(fold_data)

# ---------------------------------------------------------------------------
# Aggregate metrics
# ---------------------------------------------------------------------------
aggregate_ar = _finite_float(aggregate.get("mean_annualized_return"))
aggregate_ir = _finite_float(aggregate.get("mean_information_ratio"))
aggregate_dd = _finite_float(aggregate.get("worst_drawdown"))

# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

# --- Stability Score ---
if score >= 0:
    label = _stability_label(score)
    color = _stability_color(score)
    bar_len = int(score * 20)
    bar = "█" * bar_len + "░" * (20 - bar_len)
    st.markdown(
        f"""<div style="margin-bottom:24px;">
        <span class="qv2-text-card-label">稳定性评分</span><br>
        <span style="font-size:2rem;font-weight:800;color:var(--{color});">{score:.2f}</span>
        <span style="color:var(--text-secondary);font-size:1rem;"> / 1.00</span>
        <span style="margin-left:12px;color:var(--text-tertiary);font-size:0.9rem;">{label}</span>
        <div style="font-family:monospace;margin-top:4px;color:var(--text-tertiary);">{bar}</div>
        </div>""",
        unsafe_allow_html=True,
    )

# --- KPI row ---
kpi_cols = st.columns(4)
with kpi_cols[0]:
    mean_ir = _mean(ir_list)
    displayed_mean_ir = mean_ir if mean_ir is not None else 0
    std_ir = math.sqrt(sum((s - displayed_mean_ir) ** 2 for s in ir_list) / len(ir_list)) if len(ir_list) > 1 else 0
    render_stat_card(
        "平均 IR",
        f"{displayed_mean_ir:.2f}" if mean_ir is not None else MISSING,
        secondary=[
            ("± 标准差", f"{std_ir:.2f}" if ir_list else MISSING),
            ("区间", f"{min(ir_list):.2f} ~ {max(ir_list):.2f}" if ir_list else MISSING),
        ],
        tooltip="所有折的平均信息比率。标准差越小越一致。",
    )
with kpi_cols[1]:
    worst_idx, worst_dd = min(drawdown_by_fold, key=lambda item: item[1]) if drawdown_by_fold else (None, None)
    render_stat_card(
        "最差回撤",
        format_percent(worst_dd) if worst_dd is not None else MISSING,
        value_color="negative" if worst_dd is not None else "default",
        secondary=[("出现于折", str(worst_idx) if worst_idx is not None else MISSING)],
        tooltip="所有折中的最大回撤，定位最薄弱的窗口。",
    )
with kpi_cols[2]:
    render_stat_card(
        "整体样本外",
        format_percent(aggregate_ar) if aggregate_ar is not None else MISSING,
        value_color=("default" if aggregate_ar is None else "positive" if aggregate_ar > 0 else "negative"),
        secondary=[
            ("IR", format_number(aggregate_ir) if aggregate_ir is not None else MISSING),
            ("最差回撤", format_percent(aggregate_dd) if aggregate_dd is not None else MISSING),
        ],
        tooltip="walk_forward_report.json 里的跨折聚合指标。",
    )
with kpi_cols[3]:
    all_pos = all(s > 0 for s in ir_list) if ir_list else False
    above_1 = sum(1 for s in ir_list if s > 1.0) if ir_list else 0
    trend = "稳定" if score >= 0 and score_details.get("trend_stable", True) else "下行"
    render_stat_card(
        "鲁棒性",
        "✓ 是" if all_pos else "✗ 否",
        value_color="positive" if all_pos else "negative",
        secondary=[
            ("IR > 1.0 折数", f"{above_1}/{n_folds}"),
            ("趋势", trend),
        ],
        tooltip="全部正 = 每一折的 IR 都为正；IR > 1.0 = 多数折超过阈值。",
    )

# --- Fold comparison table ---
st.markdown("---")
st.subheader(f"折间对比（共 {n_folds} 折）")

table_rows = []
for fd in fold_data:
    table_rows.append(
        {
            "折次": f"F{fd['index']}",
            "测试期": fd.get("period", MISSING),
            "年化收益": format_percent(fd.get("annual_return")) if fd.get("annual_return") is not None else MISSING,
            "IR": format_number(fd.get("information_ratio")) if fd.get("information_ratio") is not None else MISSING,
            "最大回撤": format_percent(fd.get("max_drawdown")) if fd.get("max_drawdown") is not None else MISSING,
            "换手率": format_number(fd.get("turnover")) if fd.get("turnover") is not None else MISSING,
            "胜率": format_percent(fd.get("win_rate")) if fd.get("win_rate") is not None else MISSING,
            "交易笔数": str(fd.get("n_trades")) if fd.get("n_trades") is not None else MISSING,
        }
    )

# Summary rows
if return_list or ir_list or dd_list or turnover_list or win_rate_list:
    mean_dd = _mean(dd_list)
    mean_turnover = _mean(turnover_list)
    mean_win_rate = _mean(win_rate_list)
    mean_return = _mean(return_list)
    table_rows.append(
        {
            "折次": "均值",
            "测试期": "",
            "年化收益": format_percent(mean_return) if mean_return is not None else MISSING,
            "IR": format_number(_mean(ir_list)) if ir_list else MISSING,
            "最大回撤": format_percent(mean_dd) if mean_dd is not None else MISSING,
            "换手率": format_number(mean_turnover) if mean_turnover is not None else MISSING,
            "胜率": format_percent(mean_win_rate) if mean_win_rate is not None else MISSING,
            "交易笔数": "",
        }
    )

df = pd.DataFrame(table_rows)
st.dataframe(df, hide_index=True, height=400)

# --- Stability Breakdown ---
if score >= 0:
    with st.expander("稳定性分解", expanded=False):
        b_cols = st.columns(2)
        with b_cols[0]:
            cv = score_details.get("ir_cv", 0)
            st.caption("IR 变异系数（越低越好）")
            st.progress(min(1.0, max(0.0, 1.0 - cv)), text=f"CV = {cv:.2f}")

            st.caption("正收益折数")
            pos_str = score_details.get("positive_folds", "?/?")
            pos_ratio = _ratio_fraction(pos_str)
            st.progress(pos_ratio, text=pos_str)
        with b_cols[1]:
            dc = score_details.get("dd_concentration", 0.5)
            st.caption("回撤集中度")
            st.progress(dc, text=f"{dc:.2f}")

            st.caption("IR > 1.0 折数")
            abv_str = score_details.get("above_ir_1", "?/?")
            abv_ratio = _ratio_fraction(abv_str)
            st.progress(abv_ratio, text=abv_str)

# ---------------------------------------------------------------------------
# Bottom section — tabs (TICKET-B reorg)
# ---------------------------------------------------------------------------
st.markdown("---")

wf_tabs = st.tabs(
    [
        "拼接样本外净值",
        "单折详情",
        "指标柱图",
        "日志",
        "配置",
        "原始 JSON",
        "图表",
    ]
)

# --- Stitched OOS NAV tab -----------------------------------------------------
with wf_tabs[0]:
    timeline, nav_values, fold_bands = _synthesised_stitched_nav(fold_data)
    if not timeline:
        render_empty_state(
            "\U0001f4c8",
            "无法生成拼接净值",
            "至少一折缺少测试窗或年化收益，缺少这些字段就无法合成样本外净值曲线。",
        )
    else:
        try:
            import plotly.graph_objects as go

            fig = go.Figure()
            fig.add_trace(
                go.Scatter(
                    x=timeline,
                    y=nav_values,
                    mode="lines",
                    name="OOS NAV (synthesised)",
                    line={"width": 2.4, "color": PLOTLY_STRATEGY_COLOR},
                )
            )
            # Alternating fold shading so the operator can see the fold
            # boundaries at a glance. Light/dark alternation keeps it
            # readable without fighting the chart colours.
            for index, (fb_start, fb_end, ordinal) in enumerate(fold_bands):
                fig.add_vrect(
                    x0=fb_start,
                    x1=fb_end,
                    fillcolor=(
                        PLOTLY_FOLD_BAND_DARK
                        if index % 2 == 0
                        else PLOTLY_FOLD_BAND_LIGHT
                    ),
                    line_width=0,
                    annotation_text=f"F{ordinal}",
                    annotation_position="top left",
                    annotation_font_size=10,
                )
            fig.update_layout(
                height=380,
                margin={"t": 10, "b": 36, "l": 40, "r": 12},
                xaxis_title="测试窗",
                yaxis_title="样本外净值（×）",
                showlegend=False,
                title={
                    "text": "拼接样本外净值（合成）",
                    "font": {"size": 12},
                    "x": 0,
                },
            )
            st.plotly_chart(fig, use_container_width=True)
            st.caption(
                "由每折的年化收益与测试窗长度合成 —— 折内路径不可得"
                "（滚动验证引擎没有按折落盘 nav.parquet）。"
            )
        except ImportError:
            st.info("未安装 Plotly，净值图不可用。")

# --- Per-Fold Detail tab ------------------------------------------------------
with wf_tabs[1]:
    if not fold_data:
        render_empty_state(
            "\U0001f4ca",
            "暂无单折数据",
            "未加载到单折报告。",
        )
    else:
        # Selector lets the operator focus on one fold at a time instead
        # of scrolling through every expander. Default: fold 1.
        fold_pick_options = [f"第 {fd['index']} 折  ·  {fd.get('period', MISSING)}" for fd in fold_data]
        picked_idx = st.selectbox(
            "选择折",
            options=list(range(len(fold_data))),
            format_func=lambda i: fold_pick_options[i],
            key="wf_fold_picker",
        )
        fd = fold_data[picked_idx]

        fc1, fc2, fc3, fc4 = st.columns(4)
        with fc1:
            st.metric("年化收益", format_percent(fd.get("annual_return")))
        with fc2:
            st.metric("IR", format_number(fd.get("information_ratio")))
        with fc3:
            st.metric("最大回撤", format_percent(fd.get("max_drawdown")))
        with fc4:
            st.metric("换手率", format_number(fd.get("turnover")))

        if fd.get("train_period") or fd.get("test_period"):
            st.caption(
                f"训练期：{fd.get('train_period', MISSING)}  |  "
                f"测试期：{fd.get('test_period', MISSING)}"
            )
        elif fd.get("train_start"):
            st.caption(
                f"训练期：{fd['train_start']} → {fd.get('test_start', '?')}  |  "
                f"测试期：{fd.get('test_start', '?')} → {fd.get('test_end', '?')}"
            )

        with st.expander("单折原始报告", expanded=False):
            st.json(dict(folds[picked_idx]) if picked_idx < len(folds) else {})

# --- Metric Bars tab ----------------------------------------------------------
with wf_tabs[2]:
    try:
        import plotly.graph_objects as go

        fold_labels = [f"F{fd['index']}" for fd in fold_data]
        ar_vals = [fd.get("annual_return") for fd in fold_data]
        ir_vals = [fd.get("information_ratio") for fd in fold_data]
        dd_vals = [fd.get("max_drawdown") for fd in fold_data]

        # Three side-by-side bar charts so the operator can eyeball
        # per-metric consistency. Drawdown rendered as positive bars
        # pointing down via negative y to match the convention.
        bar_cols = st.columns(3)
        with bar_cols[0]:
            f_ar = go.Figure()
            f_ar.add_trace(
                go.Bar(
                    x=fold_labels,
                    y=[v if v is not None else 0 for v in ar_vals],
                    marker_color=[
                        PLOTLY_POSITIVE_COLOR if (v is not None and v > 0) else PLOTLY_NEGATIVE_COLOR
                        for v in ar_vals
                    ],
                )
            )
            f_ar.update_layout(
                height=220,
                margin={"t": 28, "b": 24, "l": 36, "r": 12},
                title={"text": "年化收益", "font": {"size": 12}, "x": 0},
                yaxis={"tickformat": ".0%"},
            )
            st.plotly_chart(f_ar, use_container_width=True)
        with bar_cols[1]:
            f_ir = go.Figure()
            f_ir.add_trace(
                go.Bar(
                    x=fold_labels,
                    y=[v if v is not None else 0 for v in ir_vals],
                    marker_color=[
                        PLOTLY_POSITIVE_COLOR if (v is not None and v >= 1.0)
                        else PLOTLY_INFO_COLOR if (v is not None and v > 0)
                        else PLOTLY_NEGATIVE_COLOR
                        for v in ir_vals
                    ],
                )
            )
            f_ir.update_layout(
                height=220,
                margin={"t": 28, "b": 24, "l": 36, "r": 12},
                title={"text": "信息比率（IR）", "font": {"size": 12}, "x": 0},
            )
            st.plotly_chart(f_ir, use_container_width=True)
        with bar_cols[2]:
            f_dd = go.Figure()
            f_dd.add_trace(
                go.Bar(
                    x=fold_labels,
                    y=[v if v is not None else 0 for v in dd_vals],
                    marker_color=PLOTLY_NEGATIVE_COLOR,
                )
            )
            f_dd.update_layout(
                height=220,
                margin={"t": 28, "b": 24, "l": 36, "r": 12},
                title={"text": "最大回撤", "font": {"size": 12}, "x": 0},
                yaxis={"tickformat": ".0%"},
            )
            st.plotly_chart(f_dd, use_container_width=True)
    except ImportError:
        st.info("未安装 Plotly，指标柱图不可用。")

# --- Logs tab -----------------------------------------------------------------
with wf_tabs[3]:
    logs = _read_log_files(run_dir)
    if not logs:
        render_empty_state(
            "\U0001f4dc",
            "暂无日志",
            "该滚动验证运行目录下还没有 stdout / stderr / runner 日志文件。",
        )
    else:
        log_tabs = st.tabs([name for name, _ in logs])
        for idx, (_name, text) in enumerate(logs):
            with log_tabs[idx]:
                st.code(text or "（空）", language="text")

# --- Config tab ---------------------------------------------------------------
with wf_tabs[4]:
    config_path = run_dir / "config.yaml"
    if config_path.is_file():
        config_text = config_path.read_text(encoding="utf-8")
        st.code(config_text, language="yaml")
        st.download_button(
            "下载 config.yaml",
            data=config_text.encode(),
            file_name="config.yaml",
            mime="text/yaml",
        )
    else:
        st.info("未找到 config.yaml。")

# --- Raw JSON tab -------------------------------------------------------------
with wf_tabs[5]:
    raw_data = wf_report if wf_report else {}
    if raw_data:
        st.json(raw_data)
    else:
        st.info("暂无原始数据可显示。")

# --- Charts tab ---------------------------------------------------------------
with wf_tabs[6]:
    try:
        charts = discover_charts(run_dir)
    except (ValueError, OSError) as exc:
        _stop_artifact_error("无法发现滚动验证图表", exc)
        charts = None
    if charts:
        for _label, path in charts.items():
            st.image(str(path), use_container_width=True)
    else:
        st.info("该运行目录下未发现已生成的图表。")
