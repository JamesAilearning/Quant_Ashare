"""Walk-Forward page — fold-by-fold results, stability analysis, and OOS NAV."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import streamlit as st

from web.operator_ui.components import (
    render_empty_state,
    render_error_state,
    render_stat_card,
)
from web.operator_ui.formatting import (
    format_date_absolute,
    format_duration,
    format_number,
    format_percent,
    format_relative_time,
)
from web.operator_ui.job_manager import JobManager
from web.operator_ui.page_header import render_breadcrumbs, render_page_header
from web.operator_ui.report_reader import (
    read_fold_reports,
    read_walk_forward_report,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MISSING = "\u2014"


def _finite_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _get_metrics(entry: dict, *keys: str) -> float | None:
    """Walk nested dicts: entry['metrics']['annual_return'] etc."""
    cur: Any = entry
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return _finite_float(cur)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _read_summary(run_dir: Path) -> dict[str, Any]:
    return _read_json(run_dir / "walk_forward_summary.json")


def _read_fold_metrics(fold_dir: Path) -> dict[str, Any]:
    return _read_json(fold_dir / "metrics.json")


def _compute_stability_score(sharpe_list: list[float], dd_list: list[float]) -> tuple[float, dict]:
    """Compute a composite stability score (0-1) from fold metrics."""
    n = len(sharpe_list)
    if n < 2:
        return 0.0, {"error": "Need at least 2 folds"}

    mean_s = sum(sharpe_list) / n
    var_s = sum((s - mean_s) ** 2 for s in sharpe_list) / n
    std_s = math.sqrt(var_s)
    cv = std_s / abs(mean_s) if mean_s != 0 else 1.0
    cv_clamped = min(cv, 1.0)

    n_positive = sum(1 for s in sharpe_list if s > 0)
    n_above_1 = sum(1 for s in sharpe_list if s > 1.0)

    # DD concentration: how concentrated is the worst drawdown?
    if len(dd_list) >= 2:
        worst = min(dd_list)  # most negative
        dd_range = max(dd_list) - worst if max(dd_list) != worst else 1.0
        dd_concentration = 1.0 - (abs(worst) / (abs(max(dd_list)) + 0.0001))
        dd_concentration = max(0.0, min(1.0, dd_concentration))
    else:
        dd_concentration = 0.5

    # Spearman trend: are later folds worse?
    if n >= 3:
        ranks = sorted(range(n), key=lambda i: sharpe_list[i])
        rank_map = {idx: rank for rank, idx in enumerate(ranks)}
        fold_ids = list(range(1, n + 1))
        fold_ranks = [rank_map[i] for i in range(n)]
        mean_fold = (n + 1) / 2
        mean_rank = (n - 1) / 2
        cov = sum((f - mean_fold) * (r - mean_rank) for f, r in zip(fold_ids, fold_ranks)) / n
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
        "sharpe_cv": cv,
        "positive_folds": f"{n_positive}/{n}",
        "above_sharpe_1": f"{n_above_1}/{n}",
        "dd_concentration": dd_concentration,
        "spearman": spearman,
        "trend_stable": trend_stable,
    }
    return min(1.0, max(0.0, score)), details


def _stability_label(score: float) -> str:
    if score >= 0.8:
        return "Highly stable"
    if score >= 0.6:
        return "Reasonably stable"
    if score >= 0.3:
        return "Inconsistent"
    return "Unstable"


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
render_breadcrumbs([("Analyze", None)])
render_page_header("Walk-Forward Detail", "Fold-by-fold results, stability analysis, and out-of-sample NAV.")

# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
jobs = JobManager.list_jobs()
wf_jobs = [
    j for j in jobs
    if j.get("mode") == "walk_forward" and j.get("run_dir")
]
run_options = {j["run_dir"]: j.get("job_id", "?") for j in wf_jobs if j.get("run_dir")}

if not run_options:
    render_empty_state(
        "\U0001f501",
        "No walk-forward runs yet",
        "Walk-forward validation tests your strategy's robustness by training "
        "on rolling time windows and evaluating each on out-of-sample data.",
        action_label="Config & Run",
    )
    st.stop()

selected = st.selectbox(
    "Run",
    options=list(run_options.keys()),
    format_func=lambda k: run_options[k],
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
except (ValueError, OSError):
    wf_report = {}
folds = wf_report.get("folds", [])

# Try to read folds from fold directories if not in report
if not folds:
    try:
        fold_reports = read_fold_reports(run_dir)
    except (ValueError, OSError):
        fold_reports = []
    if fold_reports:
        folds = fold_reports

if not folds:
    render_empty_state(
        "\U0001f4ca",
        "No fold data yet",
        "Fold reports will appear once the walk-forward run completes.",
    )
    try:
        charts = __import__("web.operator_ui.chart_reader", fromlist=["discover_charts"]).discover_charts(run_dir)
    except (ValueError, OSError):
        charts = {}
    if charts:
        st.header("Charts")
        for label, path in charts.items():
            st.image(str(path), use_container_width=True)
    st.stop()

# Try to read summary for stability metrics
try:
    summary = _read_summary(run_dir)
except (ValueError, OSError):
    summary = {}
stitched = summary.get("stitched_metrics", {}) if isinstance(summary.get("stitched_metrics"), dict) else {}
stability = summary.get("stability_metrics", {}) if isinstance(summary.get("stability_metrics"), dict) else {}

# ---------------------------------------------------------------------------
# Collect fold metrics
# ---------------------------------------------------------------------------
fold_data = []
sharpe_list: list[float] = []
return_list: list[float] = []
dd_list: list[float] = []
turnover_list: list[float] = []
win_rate_list: list[float] = []
trade_count_list: list[int] = []

for i, fold_entry in enumerate(folds):
    fd: dict[str, Any] = {"index": i + 1}

    # Direct fold entry fields from walk_forward_report.json
    fd["annual_return"] = _get_metrics(fold_entry, "annual_return") or _get_metrics(fold_entry, "annualized_return")
    fd["sharpe"] = _get_metrics(fold_entry, "sharpe") or _get_metrics(fold_entry, "sharpe_ratio")
    fd["max_drawdown"] = _get_metrics(fold_entry, "max_drawdown")
    fd["turnover"] = _get_metrics(fold_entry, "turnover_daily") or _get_metrics(fold_entry, "turnover")
    fd["win_rate"] = _get_metrics(fold_entry, "win_rate")
    fd["n_trades"] = fold_entry.get("n_trades")

    # Also try nested metrics from fold report
    m = fold_entry.get("metrics") if isinstance(fold_entry.get("metrics"), dict) else {}
    if m:
        if fd["annual_return"] is None:
            fd["annual_return"] = _get_metrics(m, "annual_return") or _get_metrics(m, "annualized_return")
        if fd["sharpe"] is None:
            fd["sharpe"] = _get_metrics(m, "sharpe") or _get_metrics(m, "sharpe_ratio")
        if fd["max_drawdown"] is None:
            fd["max_drawdown"] = _get_metrics(m, "max_drawdown")
        if fd["turnover"] is None:
            fd["turnover"] = _get_metrics(m, "turnover_daily") or _get_metrics(m, "turnover")
        if fd["win_rate"] is None:
            fd["win_rate"] = _get_metrics(m, "win_rate")
        if fd["n_trades"] is None:
            fd["n_trades"] = m.get("n_trades")

    # Train/test period labels
    fd["train_start"] = fold_entry.get("train_start", "")
    fd["test_start"] = fold_entry.get("test_start", "")
    fd["test_end"] = fold_entry.get("test_end", "")
    period = ""
    if fd["test_start"] and fd["test_end"]:
        period = f"{str(fd['test_start'])[:7]} \u2192 {str(fd['test_end'])[:7]}"
    fd["period"] = period

    fold_data.append(fd)
    if fd["sharpe"] is not None:
        sharpe_list.append(fd["sharpe"])
    if fd["annual_return"] is not None:
        return_list.append(fd["annual_return"])
    if fd["max_drawdown"] is not None:
        dd_list.append(fd["max_drawdown"])
    if fd["turnover"] is not None:
        turnover_list.append(fd["turnover"])
    if fd["win_rate"] is not None:
        win_rate_list.append(fd["win_rate"])
    if fd["n_trades"] is not None:
        trade_count_list.append(fd["n_trades"])

# ---------------------------------------------------------------------------
# Stability score
# ---------------------------------------------------------------------------
if sharpe_list and dd_list:
    score, score_details = _compute_stability_score(sharpe_list, dd_list)
    # Prefer summary if available
    if isinstance(stability.get("sharpe"), dict):
        cv_from_summary = _finite_float(stability["sharpe"].get("cv"))
        if cv_from_summary is not None:
            score_details["sharpe_cv"] = cv_from_summary
        n_pos = stability["sharpe"].get("n_positive_folds")
        if n_pos is not None:
            score_details["positive_folds"] = f"{n_pos}/{len(folds)}"
        n_above = stability["sharpe"].get("n_above_threshold")
        if n_above is not None:
            score_details["above_sharpe_1"] = f"{n_above}/{len(folds)}"
    if isinstance(summary.get("consistency"), dict):
        ss = _finite_float(summary["consistency"].get("stability_score"))
        if ss is not None:
            score = ss
else:
    score = -1.0
    score_details = {}

n_folds = len(fold_data)

# ---------------------------------------------------------------------------
# Stitched metrics
# ---------------------------------------------------------------------------
stitched_ar = _finite_float(stitched.get("annual_return"))
stitched_sharpe = _finite_float(stitched.get("sharpe_ratio"))
stitched_dd = _finite_float(stitched.get("max_drawdown"))

# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

# --- Stability Score ---
if score >= 0:
    label = _stability_label(score)
    color = _stability_color(score)
    bar_len = int(score * 20)
    bar = "\u2588" * bar_len + "\u2591" * (20 - bar_len)
    st.markdown(
        f"""<div style="margin-bottom:24px;">
        <span class="qv2-text-card-label">STABILITY SCORE</span><br>
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
    mean_s = sum(sharpe_list) / len(sharpe_list) if sharpe_list else 0
    std_s = math.sqrt(sum((s - mean_s) ** 2 for s in sharpe_list) / len(sharpe_list)) if len(sharpe_list) > 1 else 0
    render_stat_card(
        "MEAN SHARPE",
        f"{mean_s:.2f}",
        secondary=[("\u00b1 Std", f"{std_s:.2f}"), ("Range", f"{min(sharpe_list):.2f} to {max(sharpe_list):.2f}" if sharpe_list else MISSING)],
        tooltip="Average Sharpe ratio across all folds. Lower std = more consistent.",
    )
with kpi_cols[1]:
    worst_dd = min(dd_list) if dd_list else 0
    worst_idx = dd_list.index(worst_dd) + 1 if dd_list else 0
    render_stat_card(
        "WORST DRAWDOWN",
        format_percent(worst_dd),
        value_color="negative",
        secondary=[("Fold", str(worst_idx) if worst_idx else MISSING)],
        tooltip="Maximum drawdown across all folds. Identifies the weakest period.",
    )
with kpi_cols[2]:
    render_stat_card(
        "STITCHED OOS",
        format_percent(stitched_ar) if stitched_ar is not None else MISSING,
        value_color="positive" if (stitched_ar or 0) > 0 else "negative",
        secondary=[
            ("Sharpe", format_number(stitched_sharpe) if stitched_sharpe is not None else MISSING),
            ("Max DD", format_percent(stitched_dd) if stitched_dd is not None else MISSING),
        ],
        tooltip="Performance of the concatenated out-of-sample test periods. This is the true evaluation.",
    )
with kpi_cols[3]:
    all_pos = all(s > 0 for s in sharpe_list) if sharpe_list else False
    above_1 = sum(1 for s in sharpe_list if s > 1.0) if sharpe_list else 0
    trend = "Stable" if score >= 0 and score_details.get("trend_stable", True) else "Declining"
    render_stat_card(
        "ROBUSTNESS",
        "\u2713 Yes" if all_pos else "\u2717 No",
        value_color="positive" if all_pos else "negative",
        secondary=[
            (f"Above Sharpe 1.0", f"{above_1}/{n_folds}"),
            ("Trend", trend),
        ],
        tooltip="All positive = every fold made money. Above 1.0 = most folds beat threshold.",
    )

# --- Fold comparison table ---
st.markdown("---")
st.subheader(f"Fold Comparison ({n_folds} folds)")

import pandas as pd

table_rows = []
for fd in fold_data:
    table_rows.append({
        "Fold": f"F{fd['index']}",
        "Test Period": fd.get("period", MISSING),
        "AR": format_percent(fd.get("annual_return")) if fd.get("annual_return") is not None else MISSING,
        "Sharpe": format_number(fd.get("sharpe")) if fd.get("sharpe") is not None else MISSING,
        "Max DD": format_percent(fd.get("max_drawdown")) if fd.get("max_drawdown") is not None else MISSING,
        "Turnover": format_number(fd.get("turnover")) if fd.get("turnover") is not None else MISSING,
        "Win Rate": format_percent(fd.get("win_rate")) if fd.get("win_rate") is not None else MISSING,
        "Trades": str(fd.get("n_trades") or MISSING),
    })

# Summary rows
if return_list:
    table_rows.append({
        "Fold": "\u03bc", "Test Period": "",
        "AR": format_percent(sum(return_list) / len(return_list)),
        "Sharpe": format_number(sum(sharpe_list) / len(sharpe_list)),
        "Max DD": format_percent(sum(dd_list) / len(dd_list)),
        "Turnover": format_number(sum(turnover_list) / len(turnover_list)) if turnover_list else MISSING,
        "Win Rate": format_percent(sum(win_rate_list) / len(win_rate_list)) if win_rate_list else MISSING,
        "Trades": "",
    })

df = pd.DataFrame(table_rows)
st.dataframe(df, hide_index=True, height=400)

# --- Stability Breakdown ---
if score >= 0:
    with st.expander("Stability Breakdown", expanded=False):
        b_cols = st.columns(2)
        with b_cols[0]:
            cv = score_details.get("sharpe_cv", 0)
            st.caption("Sharpe CV (lower is better)")
            st.progress(min(1.0, max(0.0, 1.0 - cv)), text=f"CV = {cv:.2f}")

            st.caption("Positive folds")
            pos_str = score_details.get("positive_folds", "?/?")
            pos_ratio = float(pos_str.split("/")[0]) / float(pos_str.split("/")[1]) if "/" in pos_str else 0
            st.progress(pos_ratio, text=pos_str)
        with b_cols[1]:
            dc = score_details.get("dd_concentration", 0.5)
            st.caption("DD concentration")
            st.progress(dc, text=f"{dc:.2f}")

            st.caption("Above Sharpe 1.0")
            abv_str = score_details.get("above_sharpe_1", "?/?")
            abv_ratio = float(abv_str.split("/")[0]) / float(abv_str.split("/")[1]) if "/" in abv_str else 0
            st.progress(abv_ratio, text=abv_str)

# --- Per-fold detail cards ---
st.markdown("---")
st.subheader("Per-Fold Detail")

for fd in fold_data:
    with st.expander(
        f"Fold {fd['index']}  \u00b7  {fd.get('period', MISSING)}",
        expanded=(fd["index"] == 1),
    ):
        fc1, fc2, fc3, fc4 = st.columns(4)
        with fc1:
            st.metric("Annual Return", format_percent(fd.get("annual_return")))
        with fc2:
            st.metric("Sharpe", format_number(fd.get("sharpe")))
        with fc3:
            st.metric("Max Drawdown", format_percent(fd.get("max_drawdown")))
        with fc4:
            st.metric("Turnover", format_number(fd.get("turnover")))

        if fd.get("train_start"):
            st.caption(f"Train: {fd['train_start']} \u2192 {fd.get('test_start', '?')}  |  Test: {fd.get('test_start', '?')} \u2192 {fd.get('test_end', '?')}")

# --- Config tab ---
st.markdown("---")
with st.expander("Config Used", expanded=False):
    config_path = run_dir / "config.yaml"
    if config_path.is_file():
        config_text = config_path.read_text(encoding="utf-8")
        st.code(config_text, language="yaml")
        st.download_button("Download config.yaml", data=config_text.encode(), file_name="config.yaml", mime="text/yaml")
    else:
        st.info("config.yaml not found.")

# --- Raw JSON ---
with st.expander("Raw JSON", expanded=False):
    raw_data = wf_report if wf_report else {}
    if raw_data:
        st.json(raw_data)
    else:
        st.info("No raw data available.")

# --- Charts ---
try:
    charts = __import__("web.operator_ui.chart_reader", fromlist=["discover_charts"]).discover_charts(run_dir)
except (ValueError, OSError):
    charts = {}
if charts:
    st.divider()
    st.header("Generated Charts")
    for label, path in charts.items():
        st.image(str(path), use_container_width=True)
