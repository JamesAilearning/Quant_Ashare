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
from web.operator_ui.page_header import render_breadcrumbs, render_page_header
from web.operator_ui.report_reader import (
    read_fold_reports,
    read_walk_forward_report,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MISSING = "\u2014"

# Plotly does not resolve CSS custom properties (``var(--\u2026)``) \u2014 passing
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


def _get_metrics(entry: dict, *keys: str) -> float | None:
    """Walk nested dicts: entry['metrics']['annual_return'] etc."""
    cur: Any = entry
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return _finite_float(cur)


def _first_metric(entry: dict, *paths: tuple[str, ...]) -> float | None:
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
        try:
            end_nav = current_nav * (1.0 + float(ar)) ** years
        except (ValueError, OverflowError):
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


def _compute_stability_score(ir_list: list[float], dd_list: list[float]) -> tuple[float, dict]:
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
wf_jobs = [j for j in jobs if j.get("mode") == "walk_forward" and j.get("run_dir")]
run_options = {j["run_dir"]: j.get("job_id", "?") for j in wf_jobs if j.get("run_dir")}

if not run_options:
    render_empty_state(
        "\U0001f501",
        "No walk-forward runs yet",
        "Walk-forward validation tests your strategy's robustness by training "
        "on rolling time windows and evaluating each on out-of-sample data.",
    )
    if st.button("Config & Run"):
        st.switch_page("pages/config_run.py")
    st.stop()
    selected = str(output_path())
else:
    # If the operator clicked through from the Jobs hub, the selected
    # run id is in ``st.query_params["run_id"]`` (or stashed in
    # ``st.session_state["wf_selected_run"]`` as a fallback for clients
    # that strip query strings). Pre-select the matching run so the
    # detail page lands on the row the operator clicked, not the most
    # recent run.
    _requested_run_id = st.query_params.get("run_id", "") or str(
        st.session_state.get("wf_selected_run", "") or ""
    )
    _default_index = 0
    if _requested_run_id:
        _keys = list(run_options.keys())
        for idx, key in enumerate(_keys):
            if run_options[key] == _requested_run_id:
                _default_index = idx
                break
    selected = st.selectbox(
        "Run",
        options=list(run_options.keys()),
        format_func=lambda k: run_options[k],
        index=_default_index,
    )
    if not selected:
        st.stop()
        selected = str(output_path())

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
    _stop_artifact_error("Unable to read walk-forward report", exc)
    wf_report = {"folds": []}
folds = wf_report.get("folds", [])

# Try to read folds from fold directories if not in report
if not folds:
    try:
        fold_reports = read_fold_reports(run_dir)
    except (ValueError, OSError) as exc:
        _stop_artifact_error("Unable to read fold reports", exc)
        fold_reports = None
    if fold_reports:
        folds = fold_reports

if not folds:
    render_empty_state(
        "\U0001f4ca",
        "No fold data yet",
        "Fold reports will appear once the walk-forward run completes.",
    )
    try:
        charts = discover_charts(run_dir)
    except (ValueError, OSError) as exc:
        _stop_artifact_error("Unable to discover walk-forward charts", exc)
        charts = None
    if charts:
        st.header("Charts")
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
        period = f"{str(fd['test_start'])[:7]} \u2192 {str(fd['test_end'])[:7]}"
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
    mean_ir = _mean(ir_list)
    displayed_mean_ir = mean_ir if mean_ir is not None else 0
    std_ir = math.sqrt(sum((s - displayed_mean_ir) ** 2 for s in ir_list) / len(ir_list)) if len(ir_list) > 1 else 0
    render_stat_card(
        "MEAN IR",
        f"{displayed_mean_ir:.2f}" if mean_ir is not None else MISSING,
        secondary=[
            ("\u00b1 Std", f"{std_ir:.2f}" if ir_list else MISSING),
            ("Range", f"{min(ir_list):.2f} to {max(ir_list):.2f}" if ir_list else MISSING),
        ],
        tooltip="Average information ratio across all folds. Lower std = more consistent.",
    )
with kpi_cols[1]:
    worst_idx, worst_dd = min(drawdown_by_fold, key=lambda item: item[1]) if drawdown_by_fold else (None, None)
    render_stat_card(
        "WORST DRAWDOWN",
        format_percent(worst_dd) if worst_dd is not None else MISSING,
        value_color="negative" if worst_dd is not None else "default",
        secondary=[("Fold", str(worst_idx) if worst_idx is not None else MISSING)],
        tooltip="Maximum drawdown across all folds. Identifies the weakest period.",
    )
with kpi_cols[2]:
    render_stat_card(
        "AGGREGATE OOS",
        format_percent(aggregate_ar) if aggregate_ar is not None else MISSING,
        value_color=("default" if aggregate_ar is None else "positive" if aggregate_ar > 0 else "negative"),
        secondary=[
            ("IR", format_number(aggregate_ir) if aggregate_ir is not None else MISSING),
            ("Worst DD", format_percent(aggregate_dd) if aggregate_dd is not None else MISSING),
        ],
        tooltip="Cross-fold aggregate metrics from walk_forward_report.json.",
    )
with kpi_cols[3]:
    all_pos = all(s > 0 for s in ir_list) if ir_list else False
    above_1 = sum(1 for s in ir_list if s > 1.0) if ir_list else 0
    trend = "Stable" if score >= 0 and score_details.get("trend_stable", True) else "Declining"
    render_stat_card(
        "ROBUSTNESS",
        "\u2713 Yes" if all_pos else "\u2717 No",
        value_color="positive" if all_pos else "negative",
        secondary=[
            ("Above IR 1.0", f"{above_1}/{n_folds}"),
            ("Trend", trend),
        ],
        tooltip="All positive = every fold had positive IR. Above 1.0 = most folds beat threshold.",
    )

# --- Fold comparison table ---
st.markdown("---")
st.subheader(f"Fold Comparison ({n_folds} folds)")

table_rows = []
for fd in fold_data:
    table_rows.append(
        {
            "Fold": f"F{fd['index']}",
            "Test Period": fd.get("period", MISSING),
            "AR": format_percent(fd.get("annual_return")) if fd.get("annual_return") is not None else MISSING,
            "IR": format_number(fd.get("information_ratio")) if fd.get("information_ratio") is not None else MISSING,
            "Max DD": format_percent(fd.get("max_drawdown")) if fd.get("max_drawdown") is not None else MISSING,
            "Turnover": format_number(fd.get("turnover")) if fd.get("turnover") is not None else MISSING,
            "Win Rate": format_percent(fd.get("win_rate")) if fd.get("win_rate") is not None else MISSING,
            "Trades": str(fd.get("n_trades")) if fd.get("n_trades") is not None else MISSING,
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
            "Fold": "\u03bc",
            "Test Period": "",
            "AR": format_percent(mean_return) if mean_return is not None else MISSING,
            "IR": format_number(_mean(ir_list)) if ir_list else MISSING,
            "Max DD": format_percent(mean_dd) if mean_dd is not None else MISSING,
            "Turnover": format_number(mean_turnover) if mean_turnover is not None else MISSING,
            "Win Rate": format_percent(mean_win_rate) if mean_win_rate is not None else MISSING,
            "Trades": "",
        }
    )

df = pd.DataFrame(table_rows)
st.dataframe(df, hide_index=True, height=400)

# --- Stability Breakdown ---
if score >= 0:
    with st.expander("Stability Breakdown", expanded=False):
        b_cols = st.columns(2)
        with b_cols[0]:
            cv = score_details.get("ir_cv", 0)
            st.caption("IR CV (lower is better)")
            st.progress(min(1.0, max(0.0, 1.0 - cv)), text=f"CV = {cv:.2f}")

            st.caption("Positive folds")
            pos_str = score_details.get("positive_folds", "?/?")
            pos_ratio = _ratio_fraction(pos_str)
            st.progress(pos_ratio, text=pos_str)
        with b_cols[1]:
            dc = score_details.get("dd_concentration", 0.5)
            st.caption("DD concentration")
            st.progress(dc, text=f"{dc:.2f}")

            st.caption("Above IR 1.0")
            abv_str = score_details.get("above_ir_1", "?/?")
            abv_ratio = _ratio_fraction(abv_str)
            st.progress(abv_ratio, text=abv_str)

# ---------------------------------------------------------------------------
# Bottom section \u2014 tabs (TICKET-B reorg)
# ---------------------------------------------------------------------------
st.markdown("---")

wf_tabs = st.tabs(
    [
        "Stitched OOS NAV",
        "Per-Fold Detail",
        "Metric Bars",
        "Logs",
        "Config",
        "Raw JSON",
        "Charts",
    ]
)

# --- Stitched OOS NAV tab -----------------------------------------------------
with wf_tabs[0]:
    timeline, nav_values, fold_bands = _synthesised_stitched_nav(fold_data)
    if not timeline:
        render_empty_state(
            "\U0001f4c8",
            "Stitched NAV unavailable",
            "At least one fold is missing its test window or annualised "
            "return; the OOS curve cannot be synthesised without these.",
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
                xaxis_title="Test window",
                yaxis_title="OOS NAV (\u00d7)",
                showlegend=False,
                title={
                    "text": "Synthesised stitched OOS NAV",
                    "font": {"size": 12},
                    "x": 0,
                },
            )
            st.plotly_chart(fig, use_container_width=True)
            st.caption(
                "Synthesised from each fold's annualised return and test "
                "window length \u2014 actual intra-fold path is not available "
                "(walk-forward engine does not emit per-fold nav.parquet)."
            )
        except ImportError:
            st.info("Plotly not available; NAV plot disabled.")

# --- Per-Fold Detail tab ------------------------------------------------------
with wf_tabs[1]:
    if not fold_data:
        render_empty_state(
            "\U0001f4ca",
            "No fold data",
            "Fold reports were not loaded.",
        )
    else:
        # Selector lets the operator focus on one fold at a time instead
        # of scrolling through every expander. Default: fold 1.
        fold_pick_options = [f"Fold {fd['index']}  \u00b7  {fd.get('period', MISSING)}" for fd in fold_data]
        picked_idx = st.selectbox(
            "Select fold",
            options=list(range(len(fold_data))),
            format_func=lambda i: fold_pick_options[i],
            key="wf_fold_picker",
        )
        fd = fold_data[picked_idx]

        fc1, fc2, fc3, fc4 = st.columns(4)
        with fc1:
            st.metric("Annual Return", format_percent(fd.get("annual_return")))
        with fc2:
            st.metric("IR", format_number(fd.get("information_ratio")))
        with fc3:
            st.metric("Max Drawdown", format_percent(fd.get("max_drawdown")))
        with fc4:
            st.metric("Turnover", format_number(fd.get("turnover")))

        if fd.get("train_period") or fd.get("test_period"):
            st.caption(
                f"Train: {fd.get('train_period', MISSING)}  |  "
                f"Test: {fd.get('test_period', MISSING)}"
            )
        elif fd.get("train_start"):
            st.caption(
                f"Train: {fd['train_start']} \u2192 {fd.get('test_start', '?')}  |  "
                f"Test: {fd.get('test_start', '?')} \u2192 {fd.get('test_end', '?')}"
            )

        with st.expander("Raw fold report", expanded=False):
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
                title={"text": "Annual Return", "font": {"size": 12}, "x": 0},
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
                title={"text": "Information Ratio", "font": {"size": 12}, "x": 0},
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
                title={"text": "Max Drawdown", "font": {"size": 12}, "x": 0},
                yaxis={"tickformat": ".0%"},
            )
            st.plotly_chart(f_dd, use_container_width=True)
    except ImportError:
        st.info("Plotly not available; metric bars disabled.")

# --- Logs tab -----------------------------------------------------------------
with wf_tabs[3]:
    logs = _read_log_files(run_dir)
    if not logs:
        render_empty_state(
            "\U0001f4dc",
            "No logs available",
            "The walk-forward run directory does not contain any stdout / "
            "stderr / runner log files yet.",
        )
    else:
        log_tabs = st.tabs([name for name, _ in logs])
        for idx, (_name, text) in enumerate(logs):
            with log_tabs[idx]:
                st.code(text or "(empty)", language="text")

# --- Config tab ---------------------------------------------------------------
with wf_tabs[4]:
    config_path = run_dir / "config.yaml"
    if config_path.is_file():
        config_text = config_path.read_text(encoding="utf-8")
        st.code(config_text, language="yaml")
        st.download_button(
            "Download config.yaml",
            data=config_text.encode(),
            file_name="config.yaml",
            mime="text/yaml",
        )
    else:
        st.info("config.yaml not found.")

# --- Raw JSON tab -------------------------------------------------------------
with wf_tabs[5]:
    raw_data = wf_report if wf_report else {}
    if raw_data:
        st.json(raw_data)
    else:
        st.info("No raw data available.")

# --- Charts tab ---------------------------------------------------------------
with wf_tabs[6]:
    try:
        charts = discover_charts(run_dir)
    except (ValueError, OSError) as exc:
        _stop_artifact_error("Unable to discover walk-forward charts", exc)
        charts = None
    if charts:
        for _label, path in charts.items():
            st.image(str(path), use_container_width=True)
    else:
        st.info("No generated charts found in this run directory.")
