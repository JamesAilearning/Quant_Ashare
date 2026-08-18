"""Pure helpers for the Walk-Forward page (UI review P1-1).

Extracted from ``pages/walk_forward.py`` so the page module is a thin
Streamlit dispatch surface rather than a 1000+ line mix of metric math,
stability heuristics, OOS-NAV synthesis, log reading, and rendering.

Everything here is **pure** — no ``import streamlit`` at module body,
no ``st.X`` calls. That means each function is unit-testable in
isolation and a future refactor of the rendering side cannot
accidentally drift the metric math.
"""

from __future__ import annotations

import ast
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from src.core._yaml_loader import _expand_env_vars_in_tree

# ---------------------------------------------------------------------------
# Display sentinels + Plotly color constants
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[3]

#: 认证胜者的 preset,以及它 `extends` 的基座。两者合起来是仓库**实际钉住**
#: 的那次实验的全部旋钮——不只是服务语义,还有 `ensemble_window` 这类决定
#: 「这是哪个实验」的键(codex #444 r11)。
_CERTIFIED_PRESET_PATH = (
    _REPO_ROOT / "config" / "presets" / "csi800_cadence5_conservative.yaml"
)

#: 生产服务参数——两级绑定链的第二级,治理测试钉死它与 iso_week 复核 preset
#: **逐值相等**。与 preset 链取并集,两边都不漏。
_SERVING_PARAMS_PATH = (
    _REPO_ROOT / "config" / "serving" / "csi800_n5_production.yaml"
)

#: 族**内**的区分维度与产物落点,不是入族条件:认证对两份报告的 config
#: **恰好只差这两个键**(本机 20 份报告实测),族跨两个锚。
_FAMILY_INTERNAL_KEYS = frozenset({"rebalance_anchor", "output_dir"})

#: 结构性字段与服务侧独有字段,不参与身份比对。
_NON_IDENTITY_KEYS = frozenset({"extends", "out_dir"})


class GovernedFamilyUnavailableError(RuntimeError):
    """认证族身份读不出来。

    **绝不**退化成空要求:空要求会让 ``governed_family_mismatches`` 对**任何**
    配置都返回「无不符项」,于是权威恰恰读不到的时候,页面反而给每个运行都打上
    认证族文案(codex #444 r11)。宁可整页报错,也不能 fail-open 成乱发标签。
    """


def _load_mapping(path: Path) -> dict[str, Any]:
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:  # pragma: no cover - 环境损坏
        raise GovernedFamilyUnavailableError(f"{path} 读不出来:{exc}") from exc
    if not isinstance(loaded, dict) or not loaded:
        raise GovernedFamilyUnavailableError(
            f"{path} 不是非空映射(实际 {type(loaded).__name__})——"
            "认证族身份无从建立"
        )
    return loaded


#: 报告 config 的契约:引擎写的是 ``asdict(WalkForwardConfig)``
#: (``src/core/walk_forward/engine.py``)。
_WF_CONFIG_SOURCE = (
    _REPO_ROOT / "src" / "core" / "walk_forward" / "config.py"
)


def _reported_config_fields() -> frozenset[str]:
    """报告 config 里**会出现**的字段名。

    只留这些键参与身份比对:`provider_uri` / `region` 这类运行环境参数根本
    不在报告里,不做这道交集,认证运行自己都会因「缺这两个键」被判出族,
    标签全灭(本机实测)。

    用 ``ast`` 读源码而**不是** import 那个 dataclass:导进来会连带
    ``src.core`` 的整条链——实测把 **qlib 与 gym** 拉进这个号称
    「纯、无 streamlit」的 helper,1.19 秒、2042 个模块。UI 侧不该为了
    一份字段名付这个代价,logic 套件更不该。契约变了这里照样自动跟上。
    """
    try:
        tree = ast.parse(_WF_CONFIG_SOURCE.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:  # pragma: no cover - 仓库损坏
        raise GovernedFamilyUnavailableError(
            f"读不出报告配置契约({_WF_CONFIG_SOURCE}):{exc}"
        ) from exc
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "WalkForwardConfig":
            names = {
                stmt.target.id
                for stmt in node.body
                if isinstance(stmt, ast.AnnAssign)
                and isinstance(stmt.target, ast.Name)
            }
            if names:
                return frozenset(names)
    raise GovernedFamilyUnavailableError(
        f"{_WF_CONFIG_SOURCE} 里找不到 WalkForwardConfig 的字段"
    )


def _load_governed_family() -> dict[str, Any]:
    """入族条件 = 认证 preset 链 ∪ 生产服务参数,减去族内维度。

    刻意**不**在这里手挑键名。手挑漏过四次,每次都让一个跑偏的运行顶着
    「认证胜者」被读:`slippage_bps`(r6,5 bps 灵敏度臂冒充 20 bps 胜者)、
    `risk_constraint_scope` 与约束开关(r7)、`topk` 与
    `attribution_sleeve_grouping`(r10)、`ensemble_window` 这类实验语义(r11)。
    两个权威工件都整份取用之后,任一边新增字段都会自动进入判据。

    边界说清楚:引擎默认级的旋钮(如 `label_horizon_days`)两个工件都没钉,
    因此不在判据内——页面据此**只**报「按 N 个已钉旋钮判定」,不宣称做过
    全量比对。
    """
    preset = _load_mapping(_CERTIFIED_PRESET_PATH)
    base_rel = str(preset.get("extends") or "")
    merged: dict[str, Any] = {}
    if base_rel:
        merged.update(_load_mapping(_CERTIFIED_PRESET_PATH.parent / base_rel))
    merged.update(preset)
    merged.update(_load_mapping(_SERVING_PARAMS_PATH))
    reported = _reported_config_fields()
    identity = {
        key: _expand_env_vars_in_tree(value, source_path=_CERTIFIED_PRESET_PATH)
        if isinstance(value, str)
        else value
        for key, value in merged.items()
        if key in reported
        and key not in _FAMILY_INTERNAL_KEYS
        and key not in _NON_IDENTITY_KEYS
    }
    if not identity:
        raise GovernedFamilyUnavailableError("认证族身份为空——权威工件异常")
    return identity


_GOVERNED_FAMILY: dict[str, Any] = _load_governed_family()


def _knob_matches(actual: object, want: object) -> bool:
    """配置里的一个旋钮是否等于晋升族要求的值。

    数值按 float 比(YAML 里 ``5`` 与 ``5.0`` 都出现过),布尔按布尔比
    (``bool`` 是 ``int`` 的子类,不特判的话 ``True`` 会等于 ``1.0``),
    其余按字符串比。
    """
    if isinstance(want, bool):
        return actual is want
    if isinstance(want, (int, float)):
        try:
            return float(actual) == float(want)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return False
    return str(actual or "") == str(want)


def governed_family_mismatches(config: Mapping[str, Any]) -> list[str]:
    """本次运行**不符**晋升族的那些键(空 = 入族)。

    比的是整族语义,不是某一两个旋钮:少比一个,那个维度上跑偏的运行就会
    顶着「认证胜者」的文案被读——证据归属写错比不给判断更糟。
    """
    return [
        key
        for key, want in _GOVERNED_FAMILY.items()
        if not _knob_matches(config.get(key), want)
    ]

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


# ---------------------------------------------------------------------------
# Number / metric helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Stability-score heuristic.
#
# The composite score below is a **single-glance heuristic**, NOT a derived
# metric. Operators using it to gate a deployment SHALL also read the four
# sub-components (rendered alongside the score in the UI) — the weights and
# thresholds here were picked empirically by the original PR author, not by
# any optimisation procedure, and they trade off in non-obvious ways on
# extreme inputs.
#
# Weights — chosen to lean on the two signals operators actually use when
# triaging walk-forward stability:
#   * IR coefficient-of-variation (40%) — fold-to-fold consistency of risk-
#     adjusted return; the largest single weight because a strategy whose
#     IR swings wildly across folds is the canonical "not ready" case.
#   * Positive-period frequency (30%) — fraction of folds with IR > 0;
#     captures the "doesn't blow up out-of-sample" baseline.
#   * Drawdown concentration (20%) — how clustered the worst drawdown is
#     in a single fold; a heavy tail in one fold is preferable to a
#     uniformly bad drawdown across all folds.
#   * Trend stability (10%) — Spearman |ρ| of IR vs. fold ordinal; small
#     weight because a "fold N is worse than fold N-1" trend is hard to
#     interpret without more folds.
# Pinned as module constants so a refactor can't silently drift the
# composition; documented here so reviewers don't read the values as
# load-bearing magic numbers (UI review P1-6).
_STABILITY_W_IR_CV: float = 0.4
_STABILITY_W_POSITIVE_FOLDS: float = 0.3
_STABILITY_W_DD_CONCENTRATION: float = 0.2
_STABILITY_W_TREND_STABLE: float = 0.1

# Bucket labels — pinned similarly. Operators SHALL use the per-component
# breakdown rather than the coarse bucket for any actual gating decision.
_STABILITY_LABEL_HIGH: float = 0.8
_STABILITY_LABEL_MID: float = 0.6
_STABILITY_LABEL_LOW: float = 0.3

# Spearman absolute-value cutoff for "trend stable". 0.3 is the conventional
# small-effect threshold; pinning it makes the choice explicit.
_STABILITY_TREND_SPEARMAN_CUTOFF: float = 0.3

# Tooltip copy surfaced in the UI under the score. Lives next to the
# constants so the disclaimer stays close to the heuristic it disclaims.
STABILITY_SCORE_HEURISTIC_NOTE: str = (
    "启发式评分（仅供参考）：权重 0.4/0.3/0.2/0.1 是经验值，不来自任何"
    "优化过程。请同时参考下方四个子分量，不要单独依赖这个分数做模型上线"
    "的判断。"
)


def _compute_stability_score(
    ir_list: list[float], dd_list: list[float],
) -> tuple[float, dict[str, Any]]:
    """Compute a composite stability score (0-1) from fold metrics.

    **Heuristic, not a derived metric.** See the module-level constants
    above for the weight rationale and the disclaimer surfaced to
    operators in :data:`STABILITY_SCORE_HEURISTIC_NOTE`. The four
    sub-components in the returned ``details`` dict are the load-
    bearing display; the scalar score is a glance-aid for the dashboard
    KPI position only.
    """

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
    trend_stable = abs(spearman) < _STABILITY_TREND_SPEARMAN_CUTOFF

    score = (
        _STABILITY_W_IR_CV * (1.0 - cv_clamped)
        + _STABILITY_W_POSITIVE_FOLDS * (n_positive / n)
        + _STABILITY_W_DD_CONCENTRATION * dd_concentration
        + _STABILITY_W_TREND_STABLE * (1.0 if trend_stable else 0.0)
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
    if score >= _STABILITY_LABEL_HIGH:
        return "高度稳定"
    if score >= _STABILITY_LABEL_MID:
        return "较稳定"
    if score >= _STABILITY_LABEL_LOW:
        return "不稳定"
    return "极不稳定"


def _stability_color(score: float) -> str:
    if score >= _STABILITY_LABEL_HIGH:
        return "positive"
    if score >= _STABILITY_LABEL_MID:
        return "info"
    if score >= _STABILITY_LABEL_LOW:
        return "warning"
    return "negative"
