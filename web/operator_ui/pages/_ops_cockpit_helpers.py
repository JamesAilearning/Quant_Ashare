"""Pure helpers for the 生产运维 (ops cockpit) page.

No Streamlit imports — plain, unit-testable Python (the pages pattern:
``pages/_*_helpers.py`` pure + thin render page).

The rule every function here follows: **transcribe, derive, or say you do
not know — never assert.** This page's whole value is telling the operator
what is actually true about production, so a number it cannot establish is
worth strictly less than an honest gap.

Concretely that means:

* gate verdicts are **copied** out of artifacts whose bytes matched the
  authorizing digest — never recomputed here from the underlying metrics;
* the certification clock is whatever the rotation executor's own function
  returns (see ``web.operator_ui.recert_health``);
* the retrain window is **derived from the serving validator's own spacing
  pin**, and is labelled as derived, because this repository contains no
  machine-readable "next retrain due" anchor to read;
* the bundle staleness threshold is read off ``RecommendationConfig``
  rather than restated as a literal.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from scripts.retrain_gate_lib import (
    GATE_PROFILE,
    GATE_SCHEMA_VERSION,
    expected_gates,
)
from src.inference.ensemble_serving import (
    MEMBER_SPACING_DAYS_MAX,
    MEMBER_SPACING_DAYS_MIN,
)
from web.operator_ui.incumbent import IncumbentIdentity

PROJECT_ROOT = Path(__file__).resolve().parents[3]

# The tracked record of WHICH gate artifacts authorized the 2026-08-05
# cutover, and what their bytes were. The artifacts themselves live under the
# gitignored output/ tree (mutable, deletable); this file is in git, so it —
# not the artifact — is the authority (the same shape as the campaign
# ledger's "authority comes from the committed record" rule).
BASELINE_PATH = PROJECT_ROOT / "docs" / "promotion" / "csi800_n5_bootstrap_baseline.json"
# Tracked copies of the four artifacts. Looked up BY CONTENT (see
# ``_locate_by_digest``), never by filename: a sibling directory holds the
# superseded v1 gates, and matching those by name would show a withdrawn
# batch as if it authorized production.
EVIDENCE_DIR = (
    PROJECT_ROOT / "docs" / "research" / "evidence" / "csi800_n5_runs"
    / "bootstrap_v2_gates"
)


def _sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class GateMetric:
    """One measured quantity and the threshold it was judged against.

    Both come out of the artifact; nothing here re-judges anything. The
    margin exists because "PASS" alone hides how close a run came — the
    2026-08-05 ensemble passed ``csi500_weight`` with 0.0016 to spare.
    """

    name: str
    value: float
    limit: float
    exclusive: bool

    @property
    def margin(self) -> float:
        return self.limit - self.value

    @property
    def is_tight(self) -> bool:
        """Within 5% of the threshold — worth the operator's eye."""
        return self.limit > 0 and (self.margin / self.limit) < 0.05


@dataclass(frozen=True)
class NamedGate:
    """A single named gate, transcribed."""

    name: str
    verdict: str | None            # None = the block is absent from the artifact
    metrics: tuple[GateMetric, ...] = ()
    reasons: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class GateCard:
    """One authorizing gate artifact, as far as it can be established.

    ``evidence_intact`` False means the bytes did not match the digest the
    tracked baseline authorized — or no file with that digest was found. In
    that state the artifact's self-reported ``overall`` is NOT shown: a file
    that cannot be tied to the authorization is not evidence of anything,
    and rendering its own claim of PASS would launder it into one.
    """

    key: str                       # baseline key: "member[0]" … "ensemble"
    authorized_sha256: str
    authorized_path: str
    evidence_intact: bool
    resolved_path: str | None = None
    scope: str | None = None
    overall: str | None = None
    gates: tuple[NamedGate, ...] = ()
    missing_gates: tuple[str, ...] = ()
    subject: dict[str, Any] | None = None
    window: dict[str, Any] | None = None
    error: str | None = None


def _metrics_of(block: dict[str, Any]) -> tuple[GateMetric, ...]:
    """Pair each numeric metric with its own threshold, from the artifact.

    The artifact carries its ``thresholds`` inline, so the page never needs
    a second copy of the limits — a copy that could drift from the ones the
    gate actually applied.
    """
    thresholds = block.get("thresholds")
    if not isinstance(thresholds, dict):
        return ()
    found: list[GateMetric] = []
    for metric, value in block.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        for suffix, exclusive in (("_max_exclusive", True), ("_max", False)):
            limit = thresholds.get(f"{metric}{suffix}")
            if isinstance(limit, (int, float)) and not isinstance(limit, bool):
                found.append(GateMetric(
                    name=metric, value=float(value), limit=float(limit),
                    exclusive=exclusive))
                break
    return tuple(sorted(found, key=lambda m: m.name))


def _strings(block: dict[str, Any], key: str) -> tuple[str, ...]:
    raw = block.get(key)
    if not isinstance(raw, list):
        return ()
    return tuple(str(x) for x in raw)


def _transcribe(payload: dict[str, Any]) -> tuple[
    str | None, str | None, tuple[NamedGate, ...], tuple[str, ...]
]:
    """Copy scope / overall / per-gate verdicts out. No judging."""
    scope = payload.get("scope")
    scope_str = str(scope) if isinstance(scope, str) and scope else None
    overall = payload.get("overall")
    overall_str = str(overall) if isinstance(overall, str) and overall else None

    blocks = payload.get("gates")
    blocks = blocks if isinstance(blocks, dict) else {}
    try:
        wanted = expected_gates(scope_str) if scope_str else ()
    except Exception:
        # Unknown scope: fall back to whatever the artifact carries rather
        # than claiming a gate is missing from a set we cannot name.
        wanted = tuple(str(k) for k in blocks)

    named: list[NamedGate] = []
    for name in wanted or tuple(str(k) for k in blocks):
        block = blocks.get(name)
        if not isinstance(block, dict):
            named.append(NamedGate(name=name, verdict=None))
            continue
        verdict = block.get("verdict")
        named.append(NamedGate(
            name=name,
            verdict=str(verdict) if isinstance(verdict, str) and verdict else None,
            metrics=_metrics_of(block),
            reasons=_strings(block, "reasons"),
            notes=_strings(block, "notes"),
        ))
    missing = tuple(g.name for g in named if g.verdict is None)
    # The artifact's own missing list, if it disagrees, is additional signal.
    for extra in _strings(payload, "missing_gates"):
        if extra not in missing:
            missing = (*missing, extra)
    return scope_str, overall_str, tuple(named), missing


def _locate_by_digest(
    digest: str, recorded: str, *, evidence_dir: Path, root: Path,
) -> Path | None:
    """Find a file whose CONTENT is the authorized one.

    Tries the recorded path first, then content-scans the tracked evidence
    directory. Matching by digest rather than by name is what keeps the
    superseded v1 gate batch — same naming scheme, different bytes — from
    being displayed as the authorization.
    """
    candidate = root / recorded
    try:
        if candidate.is_file() and _sha256_of(candidate) == digest:
            return candidate
    except OSError:
        pass
    if not evidence_dir.is_dir():
        return None
    for path in sorted(evidence_dir.glob("*.json")):
        try:
            if _sha256_of(path) == digest:
                return path
        except OSError:
            continue
    return None


def read_gate_cards(
    *,
    baseline_path: Path | None = None,
    evidence_dir: Path | None = None,
    root: Path | None = None,
) -> tuple[tuple[GateCard, ...], str | None]:
    """The authorizing gate artifacts, digest-bound to the tracked baseline.

    Returns ``(cards, fatal_error)``; a fatal error means the baseline
    itself could not be read, in which case there is no authority to check
    anything against and no cards are produced.
    """
    baseline_path = baseline_path or BASELINE_PATH
    evidence_dir = evidence_dir or EVIDENCE_DIR
    root = root or PROJECT_ROOT
    try:
        baseline: Any = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return (), f"授权基线不可读({baseline_path}):{type(exc).__name__}: {exc}"
    if not isinstance(baseline, dict):
        return (), f"授权基线顶层不是 JSON object:{baseline_path}"
    authorized = baseline.get("authorized_by")
    entries = (authorized or {}).get("gate_artifacts") if isinstance(
        authorized, dict) else None
    if not isinstance(entries, dict) or not entries:
        return (), f"授权基线里没有 authorized_by.gate_artifacts:{baseline_path}"

    cards: list[GateCard] = []
    for key in sorted(entries):
        entry = entries[key]
        if not isinstance(entry, dict):
            cards.append(GateCard(
                key=str(key), authorized_sha256="", authorized_path="",
                evidence_intact=False, error="基线中的条目不是 object"))
            continue
        digest = str(entry.get("sha256", ""))
        recorded = str(entry.get("path", ""))
        if len(digest) != 64:
            cards.append(GateCard(
                key=str(key), authorized_sha256=digest, authorized_path=recorded,
                evidence_intact=False, error="基线记录的摘要不是 64 位十六进制"))
            continue
        found = _locate_by_digest(
            digest, recorded, evidence_dir=evidence_dir, root=root)
        if found is None:
            cards.append(GateCard(
                key=str(key), authorized_sha256=digest, authorized_path=recorded,
                evidence_intact=False,
                error=(f"未找到内容摘要为 {digest[:12]}… 的工件"
                       f"(已查:{recorded} 与 {evidence_dir})")))
            continue
        try:
            payload: Any = json.loads(found.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            cards.append(GateCard(
                key=str(key), authorized_sha256=digest, authorized_path=recorded,
                evidence_intact=False, resolved_path=str(found),
                error=f"工件不可解析:{type(exc).__name__}: {exc}"))
            continue
        if not isinstance(payload, dict):
            cards.append(GateCard(
                key=str(key), authorized_sha256=digest, authorized_path=recorded,
                evidence_intact=False, resolved_path=str(found),
                error="工件顶层不是 JSON object"))
            continue
        schema_err = None
        if payload.get("schema_version") != GATE_SCHEMA_VERSION:
            schema_err = (f"schema_version {payload.get('schema_version')!r} "
                          f"≠ {GATE_SCHEMA_VERSION!r}")
        elif payload.get("profile") != GATE_PROFILE:
            schema_err = (f"profile {payload.get('profile')!r} "
                          f"≠ {GATE_PROFILE!r}")
        if schema_err is not None:
            cards.append(GateCard(
                key=str(key), authorized_sha256=digest, authorized_path=recorded,
                evidence_intact=False, resolved_path=str(found),
                error=f"工件不符合门规范:{schema_err}"))
            continue
        scope, overall, named, missing = _transcribe(payload)
        subject = payload.get("subject")
        window = payload.get("window")
        cards.append(GateCard(
            key=str(key), authorized_sha256=digest, authorized_path=recorded,
            evidence_intact=True, resolved_path=str(found), scope=scope,
            overall=overall, gates=named, missing_gates=missing,
            subject=subject if isinstance(subject, dict) else None,
            window=window if isinstance(window, dict) else None))
    return tuple(cards), None


@dataclass(frozen=True)
class RetrainWindow:
    """When the NEXT ensemble member may be fit — derived, not looked up.

    This repository has no machine-readable "next retrain due" anchor; the
    only prose is 每季度末. What it DOES have is the serving validator's
    stagger pin, which every rotated manifest is checked against. So the
    admissible window is derived from that pin — and is labelled as derived
    wherever it is shown, because a due date presented as a repository fact
    would be an invention.
    """

    known: bool
    spacing_min: int = MEMBER_SPACING_DAYS_MIN
    spacing_max: int = MEMBER_SPACING_DAYS_MAX
    newest_fit_end: str | None = None
    days_since_newest: int | None = None
    opens_on: str | None = None
    closes_on: str | None = None
    state: str = "unknown"          # "before" | "open" | "closed" | "unknown"
    days_until_open: int | None = None
    days_closed: int | None = None
    gap_if_fit_today: int | None = None
    refused_if_fit_today: bool | None = None
    error: str | None = None


def retrain_window(
    incumbent: IncumbentIdentity, today: date,
) -> RetrainWindow:
    """Derive the admissible fit_end window for the next member."""
    if not incumbent.is_ensemble or not incumbent.members:
        return RetrainWindow(
            known=False,
            error=("现任不是可解析的 ensemble,无法推导下一成员窗口"
                   f"(现任形态:{incumbent.kind})"))
    raw = incumbent.members[-1].get("fit_end", "")
    try:
        newest = date.fromisoformat(str(raw))
    except ValueError:
        return RetrainWindow(
            known=False, newest_fit_end=str(raw) or None,
            error=f"最新成员 fit_end 不是合法日期:{raw!r}")

    opens = newest + timedelta(days=MEMBER_SPACING_DAYS_MIN)
    closes = newest + timedelta(days=MEMBER_SPACING_DAYS_MAX)
    gap_today = (today - newest).days
    if today < opens:
        state, until_open, closed_for = "before", (opens - today).days, None
    elif today > closes:
        state, until_open, closed_for = "closed", None, (today - closes).days
    else:
        state, until_open, closed_for = "open", None, None
    return RetrainWindow(
        known=True,
        newest_fit_end=newest.isoformat(),
        days_since_newest=gap_today,
        opens_on=opens.isoformat(),
        closes_on=closes.isoformat(),
        state=state,
        days_until_open=until_open,
        days_closed=closed_for,
        gap_if_fit_today=gap_today,
        # The same arithmetic load_ensemble_manifest applies to a rotated
        # manifest: outside the pin, the manifest will not load at all.
        refused_if_fit_today=not (
            MEMBER_SPACING_DAYS_MIN <= gap_today <= MEMBER_SPACING_DAYS_MAX),
    )


@dataclass(frozen=True)
class BundleFreshness:
    """How stale the scoring bundle is, against the SERVING refusal threshold."""

    known: bool
    tail_date: str | None = None
    days_behind: int | None = None
    max_age_days: int = 0
    headroom_days: int | None = None
    refuses_today: bool | None = None
    provider_uri: str | None = None
    message: str = ""


def serving_bundle_max_age_days() -> int:
    """The threshold the RECOMMENDER refuses on — read, not restated.

    A second literal here could drift from the one that actually blocks a
    morning run, and the page would then promise a headroom that does not
    exist.
    """
    from src.inference.daily_recommend import (  # noqa: PLC0415
        RecommendationConfig,
    )
    return int(RecommendationConfig.bundle_max_age_days)


def bundle_freshness(
    *,
    today: date,
    tail_date: str | None,
    provider_uri: str | None,
    message: str = "",
    max_age_days: int | None = None,
) -> BundleFreshness:
    limit = serving_bundle_max_age_days() if max_age_days is None else max_age_days
    if not tail_date:
        return BundleFreshness(
            known=False, max_age_days=limit, provider_uri=provider_uri,
            message=message or "bundle 尾部日期不可读")
    try:
        tail = date.fromisoformat(str(tail_date))
    except ValueError:
        return BundleFreshness(
            known=False, tail_date=str(tail_date), max_age_days=limit,
            provider_uri=provider_uri,
            message=f"bundle 尾部日期不是合法日期:{tail_date!r}")
    behind = (today - tail).days
    return BundleFreshness(
        known=True, tail_date=tail.isoformat(), days_behind=behind,
        max_age_days=limit, headroom_days=limit - behind,
        refuses_today=behind > limit, provider_uri=provider_uri,
        message=message)


@dataclass(frozen=True)
class OpsCommand:
    """A command the operator runs THEMSELVES. This page never runs one."""

    title: str
    command: str
    note: str = ""
    irreversible: bool = False


# Transcribed from docs/csi800-n5-production-runbook.md and
# docs/runbook_daily_update_scheduling.md. `$VAR` is expanded by the SHELL —
# only daily_recommend.py reads QUANT_* itself; daily_update.py and the
# gate/rotation scripts take every path as an explicit argument.
MORNING_COMMAND = OpsCommand(
    title="晨跑出单（每交易日早晨，手动）",
    command=(
        "python scripts/daily_recommend.py "
        "--ensemble-manifest $QUANT_ENSEMBLE_MANIFEST"
    ),
    note=("ensemble 模式下宇宙/节奏/topk 自动绑定 "
          "config/serving/csi800_n5_production.yaml；显式传参必须与绑定值相等。"),
)

DATA_UPDATE_COMMAND = OpsCommand(
    title="数据更新（fetch → 快照 → 重建 → 校验 → 原子换库）",
    command=(
        "python scripts/daily_update.py \\\n"
        "  --tushare-dir <tushare 原始目录> \\\n"
        "  --provider-dir $QUANT_PROVIDER_URI \\\n"
        "  --delisted-registry $QUANT_DELISTED_REGISTRY \\\n"
        "  --reference-cases tests/pit/reference_cases.yaml \\\n"
        "  --start-date 20180101"
    ),
    note=("会原子替换在用的 provider bundle。单飞锁：并发第二个进程退出 17 且不动任何东西。"
          "加 --dry-run 可只验不改。完整 Windows 计划任务写法见 "
          "docs/runbook_daily_update_scheduling.md。"),
    irreversible=True,
)

ROTATION_COMMANDS = (
    OpsCommand(
        title="① 训练新成员（GPU，操作人点火）",
        command="# 同族配置：Alpha158/LGB/csi800 + campaign 三守卫，24 个月训窗 + 3 个月 valid",
        note="训窗终点必须落在下方推导出的可接受窗口内，否则轮换产出的 manifest 加载不了。",
    ),
    OpsCommand(
        title="② 成员级门（trainer_integrity + ic_direction）",
        command=(
            "python scripts/retrain_gate.py --scope member \\\n"
            "  --member-pkl <新成员.pkl> --member-meta <新成员.pkl.meta.json> \\\n"
            "  --fit-start <训窗起> --fit-end <训窗终> \\\n"
            "  --valid-start <valid 起> --valid-end <valid 终> \\\n"
            "  --out output/retrain_gates/<季度>_member_gate.json"
        ),
        note="四个窗口参数照抄该成员训练所用 preset——门必须评的是同一个窗。",
    ),
    OpsCommand(
        title="③ 候选 manifest",
        command=(
            "python scripts/rotate_ensemble_member.py plan \\\n"
            "  --manifest $QUANT_ENSEMBLE_MANIFEST \\\n"
            "  --new-pkl <新成员.pkl> --new-meta <新成员.pkl.meta.json> \\\n"
            "  --fit-start <训窗起> --fit-end <训窗终> \\\n"
            "  --out output/retrain_gates/<季度>_candidate_manifest.json"
        ),
        note="plan 只写候选文件，不动生产 manifest。",
    ),
    OpsCommand(
        title="④ ensemble 级门（degeneracy + constraint_dry_run + serving_veto）",
        command=(
            "python scripts/retrain_gate.py --scope ensemble \\\n"
            "  --manifest output/retrain_gates/<季度>_candidate_manifest.json \\\n"
            "  --window-start <上季度首交易日> --window-end <上季度末> \\\n"
            "  --out output/retrain_gates/<季度>_ensemble_gate.json"
        ),
    ),
    OpsCommand(
        title="⑤ 轮换执行",
        command=(
            "python scripts/rotate_ensemble_member.py execute \\\n"
            "  --manifest $QUANT_ENSEMBLE_MANIFEST \\\n"
            "  --candidate output/retrain_gates/<季度>_candidate_manifest.json \\\n"
            "  --member-gate output/retrain_gates/<季度>_member_gate.json \\\n"
            "  --ensemble-gate output/retrain_gates/<季度>_ensemble_gate.json"
        ),
        note=("改写生产 manifest。两门工件必须均 PASS，任一缺失/FAIL = 执行器拒绝且零写入。"
              "执行器自动写 <manifest>.pre_rotation_<UTC时间戳> 备份；"
              "回滚 = 把备份复制回 manifest 路径，不需要其他任何操作。"),
        irreversible=True,
    ),
)
