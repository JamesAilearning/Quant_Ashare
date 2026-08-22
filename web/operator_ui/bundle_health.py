"""Bundle health banner — FU-8.

Audit found that an operator could submit a walk-forward run with a
stale or wrong-path bundle and only learn about it after 8 folds of
qlib loading produced an "empty dataset" error. PR #149 added the
``bundle_manifest.json`` contract + a programmatic freshness check
in ``WalkForwardEngine``; this module adds the **operator-visible
half**: a small banner at the top of the operator UI's jobs / results
pages that surfaces the bundle's tail_date + instrument count + a
status badge.

Three-layer design:

  1. **Resolve** the relevant ``provider_uri`` (default: read
     ``config.yaml`` at the project root and expand env vars; callers
     can override).
  2. **Summarise** the bundle's health via the existing
     ``training_guards.inspect_provider_metadata`` (richer than just
     reading ``bundle_manifest.json`` — has calendar / universe /
     validation cross-checks).
  3. **Render** a one-line Streamlit ``caption`` with a coloured
     emoji prefix (🟢 ok / 🟡 warning / 🔴 error / ⚪ unconfigured).

The pure summarise + resolve functions live here so they can be
unit-tested without a live Streamlit app; the rendering wrapper is
the thinnest possible glue.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Tolerance: this module imports from ``training_guards`` only —
# that module is the canonical metadata reader. We do NOT depend
# on ``src.data.bundle_manifest`` (which has a different + sparser
# manifest format for walk-forward freshness validation).
from web.operator_ui.training_guards import inspect_provider_metadata

_log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_FILE = PROJECT_ROOT / "config.yaml"

# 出单侧的陈旧硬闸:bundle 末日落后「外部今天」超过这么多**自然日**,
# ``recommend()`` 直接拒绝打分(``RecommendationConfig.bundle_max_age_days``)。
#
# 这里钉一份而不是导入 —— 导入 ``src.inference.daily_recommend`` 会把整个
# qlib + gym 拉进 Streamlit 进程。沿用 #454 为 artifact_schema_version 立下的
# 惯例:UI 侧钉具名常量,再用一条测试**读生产者源码做字面对齐**,生产者一改
# 而这边没跟就变红。
BUNDLE_MAX_AGE_DAYS = 14

# 待办队列把「更新失败」从 attention 升为 blocker 的界线。
#
# 取下限的一半是一条**写明的策略**,不是调出来的数字:到这一步,剩下的时间已经
# 不比已经损失的多。从下限推导而不是另写一个字面量,下限一改它自动跟着改。
BUDGET_HALF_SPENT_DAYS = BUNDLE_MAX_AGE_DAYS // 2

# bundle 的末日是 A 股交易日。用 UTC 比会在午夜前后差一天,所以固定东八区 ——
# 与 ``daily_recommend`` 因同一理由固定 ``_CN_TZ`` 一致。
_CN_TZ = timezone(timedelta(hours=8))


def today_cn() -> date:
    """东八区的今天。所有陈旧度计算的默认「今天」,测试可覆盖。"""
    return datetime.now(tz=_CN_TZ).date()


def days_until_stale_floor(
    tail_date: str | None, *, today: date | None = None,
) -> int | None:
    """距出单硬闸还剩几个自然日;末日未知时 ``None``。

    这是**剩余预算**,不是我们发明的分级线:出单侧只认
    ``bundle_max_age_days``,这里如实把它换算成操作人能直接读的一个数。
    负数与零都保留原值,好让调用方区分「刚好用完」和「早就过了」。
    """
    if not tail_date:
        return None
    try:
        tail = date.fromisoformat(tail_date)
    except ValueError:
        return None
    return BUNDLE_MAX_AGE_DAYS - ((today or today_cn()) - tail).days


# Status badges. Operators with screen readers / no-emoji terminals
# still get the textual status name; the emoji is a glance-aid only.
_BADGES = {
    "ok": "🟢",
    "warning": "🟡",
    "error": "🔴",
    "unconfigured": "⚪",
}


@dataclass(frozen=True)
class BundleHealthSummary:
    """One-line description of a qlib bundle's freshness state.

    Used by the banner renderer; also a pure-Python return value so
    test code can assert on the parsed shape without scraping
    Streamlit output.
    """

    provider_uri: str
    status: str  # one of: ok / warning / error / unconfigured
    message: str
    tail_date: str | None
    instrument_count: int | None
    #: 距出单硬闸还剩几个自然日；末日未知时 ``None``。
    #: 这个数比任何颜色都有信息量：它是**剩余预算**。
    days_until_stale_floor: int | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)
    errors: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# env-var helper (kept local — depending on src.core._yaml_loader would
# pull in the whole YAML loader for a one-line operation)
# ---------------------------------------------------------------------------


_ENV_PATTERN = re.compile(
    r"""
    \$\{
        (?P<name>[A-Za-z_][A-Za-z0-9_]*)
        (?: :- (?P<default>[^}]*) )?
    \}
    """,
    re.VERBOSE,
)


def _expand_env(value: str) -> str:
    """Expand ``${VAR}`` and ``${VAR:-default}`` in ``value``.

    Mirrors the YAML-loader contract from PR #149 but without
    importing the full loader (this module renders a UI banner; we
    don't want it to also pull in the YAML scanner). Unresolved
    references (env var missing AND no default) become the empty
    string — the banner then renders the "unconfigured" state.
    """
    def _replace(match: re.Match[str]) -> str:
        name = match.group("name")
        default = match.group("default")
        resolved = os.environ.get(name)
        if resolved is not None:
            return resolved
        return default if default is not None else ""

    return _ENV_PATTERN.sub(_replace, value)


def normalize_provider_uri(raw: str) -> str:
    """Expand ``${VAR}`` references and a leading ``~`` in a provider URI, so a
    config / operator value like ``${QUANT_PROVIDER_URI:-...}`` or ``~/qlib_data``
    resolves the same way the qlib runtime (``init_qlib_canonical`` →
    ``_normalize_provider_uri``) does before it checks the bundle exists. Public
    so callers (e.g. the 数据检视 page) need not reach for the private
    ``_expand_env``.

    Uses ``os.path.expanduser`` (NOT ``Path.expanduser``) — codex P2 on PR #254:
    ``Path.expanduser`` raises ``RuntimeError`` on a ``~baduser`` whose home does
    not resolve, which would crash a caller before its own ``exists()`` check;
    ``os.path.expanduser`` returns such a value unchanged (it then simply
    "doesn't exist"), matching both the previous behaviour and the runtime
    normaliser.
    """
    return os.path.expanduser(_expand_env(raw))


# ---------------------------------------------------------------------------
# resolve default provider_uri
# ---------------------------------------------------------------------------


def resolve_default_provider_uri(
    config_path: Path | str = DEFAULT_CONFIG_FILE,
) -> str:
    """Read ``provider_uri`` from a YAML config and expand env vars.

    Returns ``""`` when the file doesn't exist, doesn't parse, or
    doesn't have a ``provider_uri`` field — the banner renderer
    interprets that as the "unconfigured" state.

    Intentionally lenient about parse errors: a broken config.yaml
    shouldn't take down the operator UI's other pages.
    """
    path = Path(config_path)
    if not path.is_file():
        return ""
    try:
        import yaml  # noqa: PLC0415

        with path.open(encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
    except Exception as exc:  # noqa: BLE001 — best-effort
        # Stay lenient (return "" → "unconfigured" banner) but leave a
        # log trail: previously a malformed config.yaml vanished
        # silently and the operator had no way to tell a broken file
        # apart from a genuinely-absent provider_uri (UI review P2-4).
        _log.warning(
            "Could not read provider_uri from %s (%s: %s); "
            "treating as unconfigured.",
            path,
            type(exc).__name__,
            exc,
        )
        return ""
    if not isinstance(raw, dict):
        return ""
    raw_value = raw.get("provider_uri")
    if not isinstance(raw_value, str):
        return ""
    expanded = _expand_env(raw_value).strip()
    if not expanded:
        return ""
    # Codex P2 on PR #169: configs like ``provider_uri: ~/qlib_data``
    # were treated as a literal relative path because the banner
    # didn't expanduser; the bundle then "didn't exist" and the
    # banner showed a red ``error`` for a perfectly valid config
    # (the runtime ``init_qlib_canonical`` expanduser's the path
    # before calling qlib). Normalise here so the banner matches
    # runtime resolution.
    return str(Path(expanded).expanduser())


# ---------------------------------------------------------------------------
# summarise — pure
# ---------------------------------------------------------------------------


def summarise_bundle_health(
    provider_uri: str | None, *, today: date | None = None,
) -> BundleHealthSummary:
    """Inspect ``provider_uri`` and return a one-line health summary.

    The status is one of:

    * ``"unconfigured"`` — empty / whitespace provider_uri. The
      banner shows a grey badge.
    * ``"error"`` — provider_uri exists but is unusable (path
      doesn't exist, not a directory). Red badge.
    * ``"warning"`` — provider_uri is loadable but the metadata
      reader emitted warnings (e.g. missing ``calendars/`` or
      ``instruments/`` subdirs). Yellow badge.
    * ``"ok"`` — clean bundle with parseable metadata. Green badge.

    新鲜度参与判定:预算用尽(``days_until_stale_floor <= 0``)时降为
    ``error`` —— 那不是新阈值,是「出单此刻就会被拒」这个既成事实。
    预算没用尽则不因陈旧改色;提前示警交给待办队列。
    """
    raw = (provider_uri or "").strip()
    if not raw:
        return BundleHealthSummary(
            provider_uri="",
            status="unconfigured",
            message="未配置数据包（请在 config.yaml 里设置 provider_uri）。",
            tail_date=None,
            instrument_count=None,
        )

    # Same normalisation the runtime does (Codex P2 on PR #169): expand env
    # vars + ``~``. Matters when the caller passes a run-specific provider_uri
    # from a config with ``~/qlib_data``-style paths — without the expanduser
    # the banner would render a red "error" for a perfectly valid bundle.
    raw = normalize_provider_uri(raw)

    metadata = inspect_provider_metadata(raw)

    if metadata.errors:
        return BundleHealthSummary(
            provider_uri=raw,
            status="error",
            message="; ".join(metadata.errors),
            tail_date=None,
            instrument_count=None,
            errors=metadata.errors,
            warnings=metadata.warnings,
        )

    tail_date_iso = (
        metadata.coverage_end_date.isoformat()
        if metadata.coverage_end_date else None
    )

    # Banner copy is Chinese to match the rest of the operator UI
    # (UI review P2-1). The status enum stays English (it's never shown
    # to the operator — only the badge + this message are), and
    # ``metadata.warnings`` text is passed through verbatim because it
    # originates in ``training_guards`` (already localised there).
    remaining = days_until_stale_floor(tail_date_iso, today=today)

    parts: list[str] = []
    if tail_date_iso:
        parts.append(f"末日 {tail_date_iso}")
    if remaining is not None:
        # 无论绿黄红都写出来:操作人真正要的是这个数,不是颜色。
        parts.append(
            f"距出单下限还剩 {remaining} 天" if remaining > 0
            else f"已越过出单下限 {-remaining} 天"
        )
    if metadata.instrument_count is not None:
        parts.append(f"{metadata.instrument_count} 个标的")
    if metadata.warnings:
        parts.append(f"{len(metadata.warnings)} 条警告：" + "；".join(
            metadata.warnings[:2],
        ))

    if remaining is not None and remaining <= 0:
        # 出单此刻就会被 ``_assert_bundle_fresh`` 拒掉。
        status = "error"
    elif metadata.warnings:
        status = "warning"
    elif not parts:
        # Bundle exists, no errors / warnings, but also no metadata
        # files were found. This usually means "path exists but
        # isn't a qlib bundle" — surface as warning rather than ok.
        status = "warning"
        parts.append("未找到数据包元数据。")
    else:
        status = "ok"

    return BundleHealthSummary(
        provider_uri=raw,
        status=status,
        message=" | ".join(parts) if parts else "数据包可访问。",
        tail_date=tail_date_iso,
        instrument_count=metadata.instrument_count,
        days_until_stale_floor=remaining,
        warnings=metadata.warnings,
        errors=metadata.errors,
    )


# ---------------------------------------------------------------------------
# render — Streamlit wrapper
# ---------------------------------------------------------------------------


def render_bundle_health_banner(
    provider_uri: str | None = None,
    *,
    st: Any = None,
    config_path: Path | str = DEFAULT_CONFIG_FILE,
) -> BundleHealthSummary:
    """Render a one-line Streamlit caption summarising bundle health.

    Caller can pass ``provider_uri`` explicitly (typical for pages
    that already know which provider their content relates to). When
    ``provider_uri`` is None, the function reads
    ``provider_uri`` from ``config_path`` (default
    ``config.yaml`` at the project root) and expands env vars before
    rendering.

    Returns the underlying :class:`BundleHealthSummary` so callers
    that want to chain additional logic (e.g. show a help link
    when status is error) can do so without re-reading the bundle.

    The ``st`` parameter is dependency-injected so tests can pass a
    stub with a ``.caption`` method; production callers pass
    ``streamlit`` directly.
    """
    if provider_uri is None:
        provider_uri = resolve_default_provider_uri(config_path)
    summary = summarise_bundle_health(provider_uri)

    if st is None:
        return summary  # noqa: RET504 — explicit early-return for non-render path

    badge = _BADGES.get(summary.status, "❓")
    if summary.provider_uri:
        # Truncate path to 60 chars for the banner — full path is in
        # the summary tuple for callers that want the unmangled value.
        display_uri = summary.provider_uri
        if len(display_uri) > 60:
            display_uri = "…" + display_uri[-59:]
        st.caption(
            f"{badge} 数据包：``{display_uri}`` — {summary.message}",
        )
    else:
        st.caption(f"{badge} {summary.message}")
    return summary


__all__ = [
    "BundleHealthSummary",
    "_expand_env",
    "normalize_provider_uri",
    "render_bundle_health_banner",
    "resolve_default_provider_uri",
    "summarise_bundle_health",
]
