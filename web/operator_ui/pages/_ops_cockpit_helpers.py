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
import os
import shlex
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
) -> tuple[Path, bytes] | None:
    """Find a file whose CONTENT is the authorized one, and RETURN those bytes.

    Tries the recorded path first, then content-scans the tracked evidence
    directory. Matching by digest rather than by name is what keeps the
    superseded v1 gate batch — same naming scheme, different bytes — from
    being displayed as the authorization.

    Each candidate is read exactly ONCE and the digest is taken from that
    buffer, which is then what gets parsed. Hashing the path and re-reading
    it to parse would leave a window — the baseline's recorded path is under
    the mutable ``output/`` tree — in which a swapped file passes the digest
    check and then supplies the verdict that gets displayed as authorized
    (codex #431 r1; the same single-read rule the serving manifest loader
    follows).
    """
    candidates = [root / recorded]
    if evidence_dir.is_dir():
        candidates.extend(sorted(evidence_dir.glob("*.json")))
    for path in candidates:
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if hashlib.sha256(raw).hexdigest() == digest:
            return path, raw
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
        located = _locate_by_digest(
            digest, recorded, evidence_dir=evidence_dir, root=root)
        if located is None:
            cards.append(GateCard(
                key=str(key), authorized_sha256=digest, authorized_path=recorded,
                evidence_intact=False,
                error=(f"未找到内容摘要为 {digest[:12]}… 的工件"
                       f"(已查:{recorded} 与 {evidence_dir})")))
            continue
        found, raw_bytes = located
        try:
            # The SAME buffer the digest was taken from — never a re-read.
            payload: Any = json.loads(raw_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
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


def recommender_today() -> date:
    """The calendar day the RECOMMENDER judges bundle staleness against.

    ``src/inference/daily_recommend.py`` compares the bundle tail to
    ``date.today()`` — the HOST's local day, not a CN-local one. On a host
    running in UTC, CN-local midnight to 08:00 falls on the previous host
    day, so a CN clock here puts the cockpit a day ahead of the recommender
    and, exactly at the 14-day boundary, has it predict a refusal that will
    not happen (codex #431 r3).

    Deliberately NOT :func:`web.operator_ui.formatting.cn_today` — that one
    is the right clock for operator-facing date bucketing, and this one is
    the right clock for reproducing a machine decision. The retrain window
    keeps the CN clock precisely because nothing downstream judges it
    against a "today" at all.
    """
    return date.today()


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
    today: date | None = None,
    tail_date: str | None,
    provider_uri: str | None,
    message: str = "",
    max_age_days: int | None = None,
) -> BundleFreshness:
    limit = serving_bundle_max_age_days() if max_age_days is None else max_age_days
    # Default to the recommender's clock rather than the caller's: every call
    # site getting it right is weaker than there being nothing to get wrong.
    today = today if today is not None else recommender_today()
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




# The delisted registry the data update rebuilds against. Same env-var
# convention as the rest (documented default == the historical hardcoded
# path, docs/operations-env-vars.md).
ENV_DELISTED_REGISTRY = "QUANT_DELISTED_REGISTRY"
DEFAULT_DELISTED_REGISTRY = "D:/qlib_data/tushare_raw/delisted_registry.parquet"

# No env var exists for the raw tushare dump, so it stays an honest
# placeholder rather than a variable the operator's shell would expand to
# nothing.
TUSHARE_DIR_PLACEHOLDER = "<tushare 原始目录>"


def resolve_delisted_registry() -> str:
    """Delisted registry path: env override > documented default."""
    return (os.environ.get(ENV_DELISTED_REGISTRY, "").strip()
            or DEFAULT_DELISTED_REGISTRY)


# The name-change history the gates mask ST/renamed instruments with.
# `config.yaml` already expands `${QUANT_NAMECHANGE_PATH:-…}`; the default
# below is that same historical path.
ENV_NAMECHANGE_PATH = "QUANT_NAMECHANGE_PATH"
DEFAULT_NAMECHANGE_PATH = "D:/qlib_data/tushare_raw/all_namechanges.parquet"


def resolve_namechange_path() -> str:
    """Name-change history path: env override > documented default."""
    return (os.environ.get(ENV_NAMECHANGE_PATH, "").strip()
            or DEFAULT_NAMECHANGE_PATH)


# ---------------------------------------------------------------------------
# Command text. Built from the RESOLVED deployment state, not from `$VAR`
# spellings (codex #431 r1): only ``daily_recommend.py`` reads QUANT_* itself,
# so on the supported default layout — variables unset, UI resolving the
# documented defaults — a printed `$QUANT_PROVIDER_URI` expands to an empty
# string in the operator's shell and the "copyable" command silently does the
# wrong thing. Worse for the single-model opt-out, where the pointer's literal
# value is `none`: pasting `--ensemble-manifest none` gets rejected outright.
# The page already knows the real values; it should print those.
# ---------------------------------------------------------------------------

def _arg(value: object) -> str:
    r"""A resolved path, safe to paste as ONE shell argument.

    Every path here comes from a filesystem or an env override, so it may
    legitimately contain a space (``/srv/qlib bundles/live``) — raw
    interpolation would split it into two argv entries and the gate would
    silently run against something else, or a metacharacter would execute
    as shell syntax (codex #431 r3). The runbook's commands are POSIX
    (``$VAR`` expansion, ``\`` continuations), so POSIX quoting is the
    matching dialect. Ordinary paths come back unchanged.
    """
    return shlex.quote(str(value))


def morning_command(
    incumbent: IncumbentIdentity, *, model_path: str,
) -> OpsCommand:
    """The morning list command for THIS deployment's actual shape."""
    if incumbent.kind == "single":
        return OpsCommand(
            title="晨跑出单（每交易日早晨，手动）",
            command=f"python scripts/daily_recommend.py --model {_arg(model_path)}",
            note=("现任为单模型形态（QUANT_ENSEMBLE_MANIFEST 显式设为 `none`），"
                  "故为 --model 形态而非 --ensemble-manifest。"),
        )
    if not incumbent.is_ensemble:
        # Refusing to print a runnable command is the point: the incumbent
        # could not be confirmed, so any manifest path here would hand the
        # operator a command to score with a model this page just declined
        # to name.
        return OpsCommand(
            title="晨跑出单（现任不可解析，暂不给出命令）",
            command="# 现任 manifest 不可解析——命令待现任身份可确认后再给出",
            note=("本页不会印出一条指向无法确认之模型的命令。"
                  "请先修复 QUANT_ENSEMBLE_MANIFEST 指向的 manifest。"),
        )
    return OpsCommand(
        title="晨跑出单（每交易日早晨，手动）",
        command=("python scripts/daily_recommend.py "
                 f"--ensemble-manifest {_arg(incumbent.manifest_path)}"),
        note=("路径为本机已解析的现任 manifest（而非 $QUANT_ENSEMBLE_MANIFEST 的字面量："
              "该变量未设时 shell 会展开成空串）。ensemble 模式下宇宙/节奏/topk "
              "自动绑定 config/serving/csi800_n5_production.yaml；显式传参必须与绑定值相等。"),
    )


def data_update_command(
    *, provider_uri: str, delisted_registry: str,
    tushare_dir: str = TUSHARE_DIR_PLACEHOLDER,
) -> OpsCommand:
    """The bundle rebuild, with every path already resolved."""
    return OpsCommand(
        title="数据更新（fetch → 快照 → 重建 → 校验 → 原子换库）",
        command=(
            "python scripts/daily_update.py \\\n"
            f"  --tushare-dir {_arg(tushare_dir)} \\\n"
            f"  --provider-dir {_arg(provider_uri)} \\\n"
            f"  --delisted-registry {_arg(delisted_registry)} \\\n"
            "  --reference-cases tests/pit/reference_cases.yaml \\\n"
            "  --start-date 20180101"
        ),
        note=(f"daily_update.py **不读** QUANT_* 环境变量,四个路径必须显式传;"
              f"上面已填入本页解析到的值。{TUSHARE_DIR_PLACEHOLDER} 无对应环境变量,"
              "请自行填写(runbook 示例为 tushare_raw 目录)。"
              "会原子替换在用的 provider bundle;单飞锁下并发的第二个进程退出 17 且不动任何东西;"
              "加 --dry-run 可只验不改。完整 Windows 计划任务写法见 "
              "docs/runbook_daily_update_scheduling.md。"),
        irreversible=True,
    )


def rotation_commands(
    manifest_path: str | None, *, provider_uri: str, namechange_path: str,
) -> tuple[OpsCommand, ...]:
    """The quarterly retrain card, pointed at the resolved manifest AND the
    resolved data paths.

    ``retrain_gate.py`` defines hardcoded ``--provider``/``--namechange``
    defaults that BOTH scopes consume, while the gate artifact records
    NEITHER path. So on a deployment that overrides the bundle, an omitted
    flag silently gates against a different bundle than the one this page is
    describing — and the resulting PASS artifact can later authorize a
    production rotation with nothing downstream able to detect the mismatch
    (codex #431 r2). Print both flags explicitly.
    """
    target = manifest_path or "<现任 manifest（当前不可解析）>"
    data_flags = (f"  --provider {_arg(provider_uri)} \\\n"
                  f"  --namechange {_arg(namechange_path)} \\\n")
    return (
        OpsCommand(
            title="① 训练新成员（GPU，操作人点火）",
            command=("# 同族配置：Alpha158/LGB/csi800 + campaign 三守卫，"
                     "24 个月训窗 + 3 个月 valid"),
            note="训窗终点必须落在上方推导出的可接受窗口内，否则轮换产出的 manifest 加载不了。",
        ),
        OpsCommand(
            title="② 成员级门（trainer_integrity + ic_direction）",
            command=(
                "python scripts/retrain_gate.py --scope member \\\n"
                "  --member-pkl <新成员.pkl> --member-meta <新成员.pkl.meta.json> \\\n"
                "  --fit-start <训窗起> --fit-end <训窗终> \\\n"
                "  --valid-start <valid 起> --valid-end <valid 终> \\\n"
                + data_flags
                + "  --out output/retrain_gates/<季度>_member_gate.json"
            ),
            note="四个窗口参数照抄该成员训练所用 preset——门必须评的是同一个窗。",
        ),
        OpsCommand(
            title="③ 候选 manifest",
            command=(
                "python scripts/rotate_ensemble_member.py plan \\\n"
                f"  --manifest {_arg(target)} \\\n"
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
                + data_flags
                + "  --out output/retrain_gates/<季度>_ensemble_gate.json"
            ),
        ),
        OpsCommand(
            title="⑤ 轮换执行",
            command=(
                "python scripts/rotate_ensemble_member.py execute \\\n"
                f"  --manifest {_arg(target)} \\\n"
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
