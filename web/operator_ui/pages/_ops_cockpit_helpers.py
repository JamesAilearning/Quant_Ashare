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

import functools
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from scripts.retrain_gate_lib import (
    GATE_PROFILE,
    GATE_SCHEMA_VERSION,
    PASS,
    expected_gates,
)
from src.inference.ensemble_serving import (
    MEMBER_SPACING_DAYS_MAX,
    MEMBER_SPACING_DAYS_MIN,
)
from web.operator_ui.config_forms import (
    DEFAULT_NAMECHANGE_PATH as DEFAULT_NAMECHANGE_PATH,
)
from web.operator_ui.config_forms import (
    resolve_namechange_path as resolve_namechange_path,
)
from web.operator_ui.incumbent import PROJECT_ROOT as _INCUMBENT_ROOT
from web.operator_ui.incumbent import IncumbentIdentity
from web.operator_ui.incumbent import anchored_to_repo as anchored_to_repo
from web.operator_ui.incumbent import (
    unusable_path_reason as unusable_path_reason,
)

# The checkout — reused, not derived a second time (r23/r26/r27).
PROJECT_ROOT = Path(_INCUMBENT_ROOT)

# `resolve_default_provider_uri()` is deliberately lenient — a missing,
# unparsable, or provider_uri-less config.yaml yields "" so the rest of the
# operator UI keeps working. For THIS page that empty string is not a usable
# input but a silently WRONG one: `Path("")` is `Path(".")`, so every reader
# and every rendered command would quietly retarget the operator's working
# directory. Nothing here may treat it as a bundle location (codex #431 r21).
UNRESOLVED_PROVIDER_REASON = (
    "本页没有解析出 provider 路径(config.yaml 缺失 / 无法解析 / 无 provider_uri)"
    "——未去读任何 bundle,故无结论可报"
)


def provider_is_resolved(provider_uri: str | None) -> bool:
    """Whether a provider path was actually resolved (not blank)."""
    return bool((provider_uri or "").strip())

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


# What the operator should SEE for a card. Kept out of the page because the
# page threw the transcribed verdict away when it picked a colour: an
# artifact reporting `overall: FAIL` with no missing gates rendered as a
# GREEN success — and, with a tight metric, as a yellow banner whose text
# said 通过 (codex #431 r4). Transcribing a verdict faithfully into the data
# and then contradicting it in the presentation is worse than not showing it.
GATE_STATUS_BROKEN = "evidence_broken"     # cannot be tied to the authorization
GATE_STATUS_MISSING = "missing_gates"      # a required gate has no verdict
GATE_STATUS_FAILED = "verdict_not_pass"    # the artifact itself says not-PASS
GATE_STATUS_TIGHT = "pass_tight_margin"    # PASS, but something is near a limit
GATE_STATUS_OK = "pass"

GATE_STATUSES: tuple[str, ...] = (
    GATE_STATUS_BROKEN, GATE_STATUS_MISSING, GATE_STATUS_FAILED,
    GATE_STATUS_TIGHT, GATE_STATUS_OK,
)


def gate_card_status(card: GateCard) -> str:
    """The single status the card must be rendered as.

    Precedence, stated rather than left to branch order:

    1. evidence that cannot be bound to the authorization outranks every
       claim the artifact makes about itself;
    2. a missing gate outranks the summary verdict — an absent judgement is
       not a passing one;
    3. **any** non-PASS verdict — the summary's or a named gate's — outranks
       a tight margin. ``overall`` absent counts as not-PASS: an artifact
       that declines to state a verdict has not passed;
    4. only an all-PASS card may be shown as passing, and then a tight
       margin still downgrades it from green.
    """
    if not card.evidence_intact:
        return GATE_STATUS_BROKEN
    if card.missing_gates:
        return GATE_STATUS_MISSING
    if card.overall != PASS:
        return GATE_STATUS_FAILED
    if any(gate.verdict != PASS for gate in card.gates):
        return GATE_STATUS_FAILED
    if any(m.is_tight for gate in card.gates for m in gate.metrics):
        return GATE_STATUS_TIGHT
    return GATE_STATUS_OK


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
    # The bundle-health verdict on everything OTHER than age. The recommender
    # runs further preconditions after its age check — a fresh-dated bundle
    # stamped ``built_from_holey_fetch`` is refused by
    # ``_assert_bundle_fetch_complete`` — so age alone must never be rendered
    # as "usable" (codex #431 r5).
    health_status: str = "ok"
    health_warnings: tuple[str, ...] = ()
    # The recommender's OWN integrity gate (see recommender_integrity_check).
    # None = not evaluated.
    integrity_accepted: bool | None = None
    integrity_reason: str = ""

    @property
    def age_ok(self) -> bool:
        """Passes the AGE check only. Not a verdict on the whole bundle."""
        return self.known and self.refuses_today is False

    @property
    def usable(self) -> bool:
        """Every precondition this page can evaluate says yes.

        Each contributor is evaluated by the RECOMMENDER's own predicate —
        age by ``_bundle_is_stale``'s arithmetic on its calendar tail,
        integrity by ``read_bundle_integrity`` under the gate's own rules.
        The informational health summary can only withhold "usable", never
        grant it: it is deliberately forgiving and cannot stand in for a
        gate (codex #431 r6).

        Still NOT a promise that today's run succeeds — the recommender has
        preconditions beyond these. The page says so.
        """
        return (self.age_ok
                and self.integrity_accepted is True
                and self.health_status == "ok"
                and not self.health_warnings)


# The bundle producer writes canonical YYYY-MM-DD rows. Shape-check BEFORE
# parsing: date.fromisoformat is far more permissive than the file contract.
# Characters str.splitlines() ALSO treats as line breaks. The producer writes
# one date per LF/CRLF-terminated line; any of these would make splitlines()
# see a different calendar than the bytes literally contain (codex #431 r11).
_OTHER_LINE_BREAKS: tuple[tuple[str, str], ...] = (
    ("CR(孤立回车)", "\r"),
    ("VT(垂直制表)", "\v"),
    ("FF(换页)", "\f"),
    ("NEL", "\x85"),
    ("LS(行分隔符)", "\u2028"),
    ("PS(段分隔符)", "\u2029"),
)

_CANONICAL_DAY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


@dataclass(frozen=True)
class CalendarTail:
    """The bundle's last trading day, or an honest refusal to name one."""

    known: bool
    tail: date | None = None
    reason: str = ""


def bundle_calendar_tail(provider_uri: str) -> CalendarTail:
    """The last trading day — ONLY when the calendar bytes are unambiguous.

    The recommender takes ``calendar[-1]`` from **qlib's** provider calendar
    (``D.calendar()``), which needs ``qlib.init()``. A read-only page cannot
    do that — it is heavyweight, and this page is barred from importing qlib
    at all. So this does NOT claim to be the recommender's parser, and must
    not be described as its path (codex #431 r7).

    What it does instead: read the same file and refuse to answer unless the
    content is unambiguous — non-empty, every row a valid ISO date, strictly
    increasing, no duplicates, no blank rows before the end. Under those
    conditions every reasonable parser agrees on the last element. Outside
    them the page says it does not know, rather than hand out a tail qlib
    might not share.

    Deliberately NOT ``training_guards._read_calendar_dates``: that one
    silently DROPS malformed rows and sorts/dedupes the survivors, so a
    corrupt calendar still yields a confident (possibly wrong) tail — fine
    for an informational banner, wrong for a refusal prediction.

    This is SOUND, not exact: it may answer "unknown" where qlib would be
    fine. It must never do the reverse — which is why rows must match the
    CANONICAL ``YYYY-MM-DD`` spelling the bundle producer writes, not merely
    be something ``date.fromisoformat`` will swallow. That function also
    accepts ``2026-W32-1`` and ``20260803`` (both → 2026-08-03), and qlib's
    calendar parser need not; accepting them here would hand out a
    confident tail for bytes ``D.calendar()`` rejects, breaking the very
    guarantee this docstring makes (codex #431 r8).

    The shape check runs on the RAW row. Stripping first and validating the
    stripped value would certify bytes nobody validated (codex #431 r9) —
    the rule is: **validate what is actually in the file, never a
    normalization of it.**
    """
    if not provider_uri.strip():
        # `Path("") / "calendars" / "day.txt"` is `./calendars/day.txt` — the
        # WORKING DIRECTORY's, not the deployment's. Reading it would report
        # 读不到交易日历, blaming a bundle this function never located, when
        # the true fault is upstream: no provider path was resolved at all
        # (codex #431 r21).
        return CalendarTail(known=False, reason=UNRESOLVED_PROVIDER_REASON)
    _foreign = unusable_path_reason(provider_uri)
    if _foreign is not None:
        # Reading it would answer about `<cwd>/D:/…` — a location that
        # depends on who is asking, not on the deployment (codex #431 r30).
        return CalendarTail(known=False, reason=_foreign)
    path = Path(provider_uri) / "calendars" / "day.txt"
    try:
        # BYTES, not read_text(): universal-newline decoding silently folds a
        # lone CR into LF, which would accept a separator this contract does
        # not list (codex #431 r11).
        raw = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        # UnicodeDecodeError is a ValueError, NOT an OSError — corrupt or
        # partially-copied bytes would otherwise escape and take the whole
        # Streamlit page down with a traceback instead of producing the
        # 无法判定 state this function promises (codex #431 r10).
        return CalendarTail(
            known=False, reason=f"读不到交易日历 {path}:{type(exc).__name__}")

    # CRLF is the real production bundle's terminator; LF is the other
    # supported one. Everything else stays and is refused below.
    text = raw.replace("\r\n", "\n")
    for name, char in _OTHER_LINE_BREAKS:
        if char in text:
            return CalendarTail(
                known=False,
                reason=(f"交易日历含 {name} 分隔符——本契约只支持 LF / CRLF;"
                        "str.splitlines() 会把它也当换行,而 qlib 未必如此"))
    rows = text.split("\n")
    # Exactly ONE trailing newline is allowed (the file's final terminator).
    if rows and rows[-1] == "":
        rows.pop()
    if not rows:
        return CalendarTail(known=False, reason=f"交易日历为空:{path}")
    parsed: list[date] = []
    for index, value in enumerate(rows, start=1):
        if not value:
            return CalendarTail(
                known=False,
                reason=f"交易日历第 {index} 行为空行,内容有歧义,不据此判定")
        if not _CANONICAL_DAY_RE.fullmatch(value):
            return CalendarTail(
                known=False,
                reason=(f"交易日历第 {index} 行不是规范的 YYYY-MM-DD 写法"
                        f"({value!r};含首尾空白也算);本页不猜测 qlib 会如何解读它"))
        try:
            parsed.append(date.fromisoformat(value))
        except ValueError:
            return CalendarTail(
                known=False,
                reason=(f"交易日历第 {index} 行不是合法日期({value!r});"
                        "本页不猜测 qlib 会如何解读它"))
    for index in range(1, len(parsed)):
        if parsed[index] <= parsed[index - 1]:
            return CalendarTail(
                known=False,
                reason=(f"交易日历第 {index + 1} 行未严格递增"
                        f"({parsed[index - 1]} → {parsed[index]});内容有歧义"))
    return CalendarTail(known=True, tail=parsed[-1],
                        reason="交易日历字节无歧义")


@dataclass(frozen=True)
class BundleIntegrityCheck:
    """The recommender's fetch-integrity gate, evaluated by ITS reader.

    ``summarise_bundle_health`` is informational and deliberately forgiving:
    ``training_guards`` swallows a bad stamp (``except Exception`` — "the UI
    banner must not crash on a bad stamp") and falls back to
    ``validation.json``/``manifest.json``, so a bundle whose
    ``_fetch_integrity.json`` is MISSING or CORRUPT can come back with no
    warnings at all. ``_assert_bundle_fetch_complete`` refuses both
    (codex #431 r6).

    So this does not approximate that gate — it runs
    ``read_bundle_integrity``, the very reader the gate uses, and applies the
    gate's own three rules: corrupt is refused unconditionally; missing and
    holey are refused unless the operator passes ``--allow-holey-recommend``.
    """

    known: bool
    accepted: bool | None = None
    reason: str = ""
    holey: bool | None = None
    built_at: str | None = None


def recommender_integrity_check(
    provider_uri: str, *, allow_holey: bool = False,
) -> BundleIntegrityCheck:
    """Would ``daily_recommend`` accept this bundle's integrity stamp?"""
    from src.data.pit.bundle_integrity import (  # noqa: PLC0415
        BundleIntegrityError,
        read_bundle_integrity,
    )

    _foreign = unusable_path_reason(provider_uri)
    if _foreign is not None:
        # Same rule as the calendar reader: a spelling this host resolves
        # against its own CWD names no single bundle (codex #431 r30).
        return BundleIntegrityCheck(known=False, reason=_foreign)
    if not provider_uri.strip():
        # Without this, the normalizer turns "" into the CWD and the reader
        # finds no stamp there — returning `known=True, accepted=False`, i.e.
        # a CONFIDENT refusal verdict about a bundle that was never located.
        # Claiming a verdict on something you did not examine is the exact
        # failure this page exists to prevent (codex #431 r21).
        # `accepted` stays None — this type's own spelling for "not
        # evaluated". Writing False here would hand a direct consumer a
        # definite refusal for a bundle nothing inspected, which is the very
        # thing this branch exists to stop; the sibling `known=False` branch
        # below already leaves it unset (codex #431 r22).
        return BundleIntegrityCheck(
            known=False, reason=UNRESOLVED_PROVIDER_REASON)

    # The gate normalizes the URI before reading (expanduser/abspath/realpath/
    # normcase) — a `~/…` or whitespaced URI would otherwise read a
    # non-existent literal path and a clean bundle would look unstamped.
    # Reuse that same normalization rather than a second one.
    from src.inference import daily_recommend as _rec  # noqa: PLC0415
    try:
        stamp = read_bundle_integrity(
            Path(_rec._normalize_provider_uri(  # type: ignore[attr-defined]
                provider_uri)))
    except BundleIntegrityError as exc:
        # Refused REGARDLESS of the override — the override accepts
        # incompleteness (a known state), never corruption.
        return BundleIntegrityCheck(
            known=True, accepted=False,
            reason=f"完整性 stamp 损坏/不可读,出单侧无条件拒绝:{exc}")
    except OSError as exc:  # pragma: no cover - defensive
        return BundleIntegrityCheck(
            known=False, reason=f"无法读取完整性 stamp:{type(exc).__name__}: {exc}")
    if stamp is None:
        return BundleIntegrityCheck(
            known=True, accepted=allow_holey,
            reason=("缺 _fetch_integrity.json——无法确认 bundle 建自完整 fetch;"
                    "出单侧拒绝(除非显式 --allow-holey-recommend)"))
    if stamp.built_from_holey_fetch:
        return BundleIntegrityCheck(
            known=True, accepted=allow_holey, holey=True,
            built_at=stamp.built_at,
            reason=("stamp 标记 built_from_holey_fetch=true;"
                    "出单侧拒绝(除非显式 --allow-holey-recommend)"))
    return BundleIntegrityCheck(
        known=True, accepted=True, holey=False, built_at=stamp.built_at,
        reason="完整性 stamp 完好且非 holey")


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
    health_status: str = "ok",
    health_warnings: tuple[str, ...] = (),
    integrity_accepted: bool | None = None,
    integrity_reason: str = "",
) -> BundleFreshness:
    limit = serving_bundle_max_age_days() if max_age_days is None else max_age_days
    # Default to the recommender's clock rather than the caller's: every call
    # site getting it right is weaker than there being nothing to get wrong.
    today = today if today is not None else recommender_today()
    if not tail_date:
        return BundleFreshness(
            known=False, max_age_days=limit, provider_uri=provider_uri,
            message=message or "bundle 尾部日期不可读",
            health_status=health_status, health_warnings=health_warnings,
            integrity_accepted=integrity_accepted,
            integrity_reason=integrity_reason)
    try:
        tail = date.fromisoformat(str(tail_date))
    except ValueError:
        return BundleFreshness(
            known=False, tail_date=str(tail_date), max_age_days=limit,
            provider_uri=provider_uri,
            message=f"bundle 尾部日期不是合法日期:{tail_date!r}",
            health_status=health_status, health_warnings=health_warnings,
            integrity_accepted=integrity_accepted,
            integrity_reason=integrity_reason)
    behind = (today - tail).days
    return BundleFreshness(
        known=True, tail_date=tail.isoformat(), days_behind=behind,
        max_age_days=limit, headroom_days=limit - behind,
        refuses_today=behind > limit, provider_uri=provider_uri,
        message=message, health_status=health_status,
        health_warnings=health_warnings,
        integrity_accepted=integrity_accepted,
        integrity_reason=integrity_reason)


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
    """Delisted registry path, with the CLI's EXACT env semantics.

    Raw ``os.environ.get(VAR, DEFAULT)`` — see
    :func:`web.operator_ui.incumbent.resolve_model_path` for why the page
    must not normalize where ``scripts/daily_recommend._DEFAULT_REGISTRY``
    does not (codex #431 r24).
    """
    return os.environ.get(ENV_DELISTED_REGISTRY, DEFAULT_DELISTED_REGISTRY)


# The active-stocks snapshot the recommender needs for the ST filter.
ENV_NAME_SOURCE = "QUANT_NAME_SOURCE"


def resolve_name_source() -> str:
    """Active-stocks snapshot — from the SERVING config's own default.

    ``RecommendationConfig.name_source_parquet`` is a ``default_factory``
    that reads ``QUANT_NAME_SOURCE`` itself, so calling it yields exactly
    what ``recommend()`` would use. Restating the literal here would let the
    page print ``--name-source <stale>`` on the very command whose purpose is
    to name the deployment — and an explicit flag OVERRIDES the new default,
    so the drift would not merely mislead, it would take effect
    (codex #431 r23, same class as the namechange duplicate).

    Note this also drops a normalization the page had invented: the factory
    does NOT ``.strip()`` or treat ``""`` as unset, so ``QUANT_NAME_SOURCE=""``
    now yields ``""`` here — exactly what ``recommend()`` would receive, and
    exactly what the command boundary then refuses to render.
    """
    from src.inference.daily_recommend import (  # noqa: PLC0415
        RecommendationConfig,
    )
    factory = (RecommendationConfig
               .__dataclass_fields__["name_source_parquet"].default_factory)
    if not callable(factory):                 # pragma: no cover - shape guard
        # Fail loud rather than fall back to a literal: a silent fallback is
        # how the duplicate this replaced got there in the first place.
        raise TypeError(
            "RecommendationConfig.name_source_parquet 不再是 default_factory,"
            "本页无法再复用出单侧默认值——请重接,不要在此复制字面量。")
    return str(factory())


# The name-change history the gates mask ST/renamed instruments with.
#
# RE-EXPORTED from `config_forms` (see the import block at the top of this
# module), not re-implemented: that module already owns this resolver and the
# config-run path still uses it to write job configs. A second copy here
# would let the cockpit's printed gate command and the UI-generated job
# select DIFFERENT ST histories the moment either default or its
# normalization drifted — and nothing would flag the divergence
# (codex #431 r23). Same reasoning as W1's shared incumbent resolver: two
# surfaces that can disagree about production are worse than one.


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

# Characters that make a value impossible to render safely for BOTH shells:
# a single quote (POSIX and PowerShell escape it differently) and any line
# break (which would end the single-line command and start a new one).
_UNRENDERABLE_CHARS = ("'", "\n", "\r")

# Why a resolved value cannot become a command argument. Two distinct causes,
# kept apart so the refusal the operator reads names the ACTUAL problem: a
# path this page could not resolve at all is a different repair than a path
# whose spelling no single command text can carry.
_WHY_UNRENDERABLE = "含无法跨 shell 安全表达的字符(单引号或换行)"
_WHY_UNRESOLVED = "为空——本页根本没有解析出这条路径"


class _UnusableArgument(ValueError):
    """A resolved value that must not be put into a cross-shell command.

    Carries the value and the reason for the page to DISPLAY (as text, never
    as command bytes) so the operator can still see which path is the problem
    and what would fix it.
    """

    def __init__(self, value: str, why: str) -> None:
        super().__init__(value)
        self.value = value
        self.why = why


def _refuses_unusable(fn: Any) -> Any:
    """Turn an unusable argument into a comment-only stand-in.

    Applied at the BOUNDARY so no builder has to remember: refusing is a
    property of "this page will not print an unsafe or wrong command", not
    of any single command's construction.
    """
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except _UnusableArgument as exc:
            refusal = _refused(fn.__name__, "某个已解析路径", exc.value, exc.why)
            # rotation_commands returns a tuple; the others a single command.
            returns_many = "tuple" in str(fn.__annotations__.get("return", ""))
            return (refusal,) if returns_many else refusal
    return wrapper


def _refused(
    title: str, what: str, value: str | None, why: str,
) -> OpsCommand:
    """A wholly non-runnable stand-in for a command we will not render.

    Every line is a comment in both PowerShell and POSIX, and the offending
    value (when there is one) appears only in ``note`` — rendered as page
    text, never as something the operator could paste into a shell.

    ``value=None`` is for refusals with nothing to show: the input was
    absent, not malformed.
    """
    shown = f":{value!r}" if value is not None else ""
    return OpsCommand(
        title=f"{title}（无法生成可粘贴命令）",
        command=("# 本页拒绝为该部署渲染命令。命令文本里**不含**任何已解析\n"
                 "# 取值——把有问题的取值放进来会让这段「拒绝」本身可执行。\n"
                 "# 原因与修法见下方说明。"),
        note=(f"{what}:{why}{shown}。"
              "请先修好这一项再回本页,或手工构造该命令。"),
    )


def _arg(value: object) -> str:
    r"""A resolved path, safe to paste as ONE shell argument.

    Every path here comes from a filesystem or an env override, so it may
    legitimately contain a space (``/srv/qlib bundles/live``) — raw
    interpolation would split it into two argv entries and the gate would
    silently run against something else, or a metacharacter would execute
    as shell syntax (codex #431 r3).

    Single-quoting is used because BOTH shells this page serves accept it:
    the operator runs PowerShell (this repo's documented platform) and the
    runbook is written POSIX. Verified by executing a generated command
    through ``powershell.exe`` — a space-bearing path arrives as one argv
    entry. Ordinary paths come back unchanged.

    ``cmd.exe`` is explicitly NOT in scope and must not be claimed: it does
    not treat single quotes as argument delimiters, so the same path splits
    (verified through ``shell=True``:
    ``ARGV= ['--provider-dir', "'D:/qlib", "bundles/live'"]``). Saying
    "works in cmd too" costs nothing to write and is exactly the kind of
    unverified claim this page exists to avoid (codex #431 r16).

    The one form that cannot be rendered for both is an embedded single
    quote: POSIX closes and re-opens (``'"'"'``), PowerShell doubles
    (``''``). Rather than emit something correct in one shell and silently
    wrong in the other, say so (codex #431 r15).
    """
    text = str(value)
    if not text.strip():
        # An UNRESOLVED path is not a renderable argument. `''` quotes
        # perfectly and reads as a legitimate flag value, which is exactly
        # the danger: `Path("")` is `Path(".")`, so `--provider-dir ''`
        # silently retargets the tool at the operator's WORKING DIRECTORY
        # instead of the deployment (verified: `Path("") == WindowsPath(".")`).
        # A command that runs against the wrong bundle is worse than no
        # command (codex #431 r21).
        raise _UnusableArgument(text, _WHY_UNRESOLVED)
    if any(ch in text for ch in _UNRENDERABLE_CHARS):
        # NEVER describe the offending value inside the command. The first
        # attempt did — `<路径含单引号…：{text}>` — and that text is itself
        # executable: a value like ``a'b' ; touch /tmp/x #`` closes the
        # quote, runs the command after `;`, and comments out the rest.
        # Verified locally: the file was created. A refusal that executes
        # the thing it refuses is worse than no refusal (codex #431 r20).
        raise _UnusableArgument(text, _WHY_UNRENDERABLE)
    foreign = unusable_path_reason(text)
    if foreign is not None:
        # Quotes fine, reads fine — and means a DIFFERENT place depending on
        # who resolves it. Refused at the same boundary as the other two, so
        # no builder has to remember (codex #431 r30).
        raise _UnusableArgument(text, foreign)
    # UNCONDITIONAL, not shlex.quote's "does this need quoting?" judgement —
    # that judgement is POSIX's. A path named ``@bundle`` needs no quoting in
    # POSIX, so shlex returns it bare, and PowerShell then reads a leading
    # ``@`` as splatting syntax and DROPS the argument entirely (verified:
    # ``--provider-dir @bundle`` → ``ARGV= ['--provider-dir']``). Quoting
    # everything removes the whole class of per-shell metacharacter
    # disagreement instead of enumerating it (codex #431 r17).
    return f"'{text}'"


@_refuses_unusable
def morning_command(
    incumbent: IncumbentIdentity, *, model_path: str,
    provider_uri: str, delisted_registry: str, name_source: str,
    bundle_max_age_days: int,
) -> OpsCommand:
    """The morning list command for THIS deployment's actual shape.

    Names the DATA paths too, not only the model: ``daily_recommend.py``
    defines its own ``--provider-uri``/``--delisted-registry``/
    ``--name-source`` defaults from ITS environment. Streamlit may hold a
    ``QUANT_PROVIDER_URI`` the operator's terminal never inherits (a service
    unit, a different shell), or ``config.yaml`` may select another provider
    — and then the copyable command scores a LIVE list from a different
    bundle than sections ④/⑤ just reported on (codex #431 r5).
    """
    # The staleness threshold too: scripts/daily_recommend.py carries its OWN
    # argparse default (a literal 14) independent of the
    # RecommendationConfig.bundle_max_age_days that section ⑤ reads. Omit the
    # flag and the page predicts a refusal against one number while the pasted
    # command applies another (codex #431 r14).
    data_flags = (f" --provider-uri {_arg(provider_uri)}"
                  f" --delisted-registry {_arg(delisted_registry)}"
                  f" --name-source {_arg(name_source)}"
                  f" --bundle-max-age-days {bundle_max_age_days}")
    if incumbent.kind == "single":
        return OpsCommand(
            title="晨跑出单（每交易日早晨，手动）",
            command=(f"python scripts/daily_recommend.py --model {_arg(model_path)}"
                     + data_flags),
            note=("现任为单模型形态（QUANT_ENSEMBLE_MANIFEST 显式设为 `none`），"
                  "故为 --model 形态而非 --ensemble-manifest。"
                  "数据路径显式传入——CLI 有自己的默认值，不传就可能跑在另一份 bundle 上。"),
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
                 f"--ensemble-manifest {_arg(incumbent.manifest_path)}"
                 + data_flags),
        note=("路径为本机已解析的现任 manifest（而非 $QUANT_ENSEMBLE_MANIFEST 的字面量："
              "该变量未设时 shell 会展开成空串）。ensemble 模式下宇宙/节奏/topk "
              "自动绑定 config/serving/csi800_n5_production.yaml；显式传参必须与绑定值相等。"),
    )


@_refuses_unusable
def data_update_command(
    *, provider_uri: str, delisted_registry: str,
    tushare_dir: str = TUSHARE_DIR_PLACEHOLDER,
) -> OpsCommand:
    """The bundle rebuild, with every path already resolved."""
    return OpsCommand(
        title="数据更新（fetch → 快照 → 重建 → 校验 → 原子换库）",
        command=(
            "python scripts/daily_update.py "
            f"--tushare-dir {_arg(tushare_dir)} "
            f"--provider-dir {_arg(provider_uri)} "
            f"--delisted-registry {_arg(delisted_registry)} "
            "--reference-cases tests/pit/reference_cases.yaml "
            "--start-date 20180101"
        ),
        note=(f"daily_update.py **不读** QUANT_* 环境变量,四个路径必须显式传;"
              f"上面已填入本页解析到的值。{TUSHARE_DIR_PLACEHOLDER} 无对应环境变量,"
              "请自行填写(runbook 示例为 tushare_raw 目录)。"
              "会原子替换在用的 provider bundle;单飞锁下并发的第二个进程退出 17 且不动任何东西;"
              "加 --dry-run 可只验不改。完整 Windows 计划任务写法见 "
              "docs/runbook_daily_update_scheduling.md。"),
        irreversible=True,
    )


@_refuses_unusable
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

    Without a resolved ensemble manifest there is no rotation to describe, so
    the whole card is refused rather than rendered around a placeholder — see
    below.
    """
    if manifest_path is None:
        # Previously this substituted the literal `<现任 manifest（当前不可
        # 解析）>` and went on to render both gate commands AND the
        # irreversible `execute` step. Section ④ says overhead that quarterly
        # ensemble rotation does not apply here, and the page then printed the
        # workflow anyway — a single-model or unknown-incumbent operator got a
        # complete, runnable-looking rotation procedure for an ensemble that
        # does not exist. An inapplicable procedure shown as applicable is a
        # worse failure than a missing one (codex #431 r22).
        return (_refused(
            "rotation_commands", "现任 ensemble manifest 未解析", None,
            "现任不是 ensemble,或其 manifest 无法解析——季度轮换的前提不成立"),)
    target = manifest_path
    data_flags = (f"--provider {_arg(provider_uri)} "
                  f"--namechange {_arg(namechange_path)} ")
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
                "python scripts/retrain_gate.py --scope member "
                "--member-pkl <新成员.pkl> --member-meta <新成员.pkl.meta.json> "
                "--fit-start <训窗起> --fit-end <训窗终> "
                "--valid-start <valid 起> --valid-end <valid 终> "
                + data_flags
                + "--out output/retrain_gates/<季度>_member_gate.json"
            ),
            note="四个窗口参数照抄该成员训练所用 preset——门必须评的是同一个窗。",
        ),
        OpsCommand(
            title="③ 候选 manifest",
            command=(
                "python scripts/rotate_ensemble_member.py plan "
                f"--manifest {_arg(target)} "
                "--new-pkl <新成员.pkl> --new-meta <新成员.pkl.meta.json> "
                "--fit-start <训窗起> --fit-end <训窗终> "
                "--out output/retrain_gates/<季度>_candidate_manifest.json"
            ),
            note="plan 只写候选文件，不动生产 manifest。",
        ),
        OpsCommand(
            title="④ ensemble 级门（degeneracy + constraint_dry_run + serving_veto）",
            command=(
                "python scripts/retrain_gate.py --scope ensemble "
                "--manifest output/retrain_gates/<季度>_candidate_manifest.json "
                "--window-start <上季度首交易日> --window-end <上季度末> "
                + data_flags
                + "--out output/retrain_gates/<季度>_ensemble_gate.json"
            ),
        ),
        OpsCommand(
            title="⑤ 轮换执行",
            command=(
                "python scripts/rotate_ensemble_member.py execute "
                f"--manifest {_arg(target)} "
                "--candidate output/retrain_gates/<季度>_candidate_manifest.json "
                "--member-gate output/retrain_gates/<季度>_member_gate.json "
                "--ensemble-gate output/retrain_gates/<季度>_ensemble_gate.json"
            ),
            note=("改写生产 manifest。两门工件必须均 PASS，任一缺失/FAIL = 执行器拒绝且零写入。"
                  "执行器自动写 <manifest>.pre_rotation_<UTC时间戳> 备份；"
                  "回滚 = 把备份复制回 manifest 路径，不需要其他任何操作。"),
            irreversible=True,
        ),
    )
