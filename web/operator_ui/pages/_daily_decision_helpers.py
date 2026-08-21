"""Pure helpers for the 日度信号与人工决策 (daily decision) page.

No Streamlit imports here — everything is unit-testable plain Python
(the P1-1 pages pattern: ``pages/_*_helpers.py`` pure + thin render page).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Final

from scripts.eval_profiles import EVAL_PROFILES
from web.operator_ui._path_guard import output_path
from web.operator_ui.daily_signal_navigation import recommendation_artifact_date

# The incumbent resolver now lives at package level so 日度信号与人工决策 and 生产运维
# resolve production identity through the SAME code — two copies could name
# two different models. Re-exported here so this module's existing import
# surface (and the pins on it) stay unchanged.
from web.operator_ui.incumbent import (
    DEFAULT_ENSEMBLE_MANIFEST as DEFAULT_ENSEMBLE_MANIFEST,
)
from web.operator_ui.incumbent import (
    DEFAULT_MODEL_PATH as DEFAULT_MODEL_PATH,
)
from web.operator_ui.incumbent import (
    ENV_ENSEMBLE_MANIFEST as ENV_ENSEMBLE_MANIFEST,
)
from web.operator_ui.incumbent import (
    ENV_MODEL_PATH as ENV_MODEL_PATH,
)
from web.operator_ui.incumbent import (
    SINGLE_MODEL_SENTINEL as SINGLE_MODEL_SENTINEL,
)
from web.operator_ui.incumbent import (
    IncumbentIdentity as IncumbentIdentity,
)
from web.operator_ui.incumbent import (
    anchored_to_repo as anchored_to_repo,
)
from web.operator_ui.incumbent import (
    load_ensemble_manifest_identity as load_ensemble_manifest_identity,
)
from web.operator_ui.incumbent import (
    resolve_incumbent as resolve_incumbent,
)
from web.operator_ui.incumbent import (
    resolve_model_path as resolve_model_path,
)
from web.operator_ui.incumbent import (
    unusable_path_reason as unusable_path_reason,
)

# The inference producer currently writes this exact artifact schema.  Keep the
# read-only pages on one contract boundary instead of letting each page accept
# a different version vocabulary.
SUPPORTED_DAILY_RECOMMENDATION_ARTIFACT_SCHEMA_VERSION: Final[int] = 2


def artifact_schema_is_supported(payload: dict[str, Any]) -> bool:
    """Whether a persisted recommendation artifact has the supported schema."""
    version = payload.get("artifact_schema_version")
    return (
        not isinstance(version, bool)
        and isinstance(version, int)
        and version == SUPPORTED_DAILY_RECOMMENDATION_ARTIFACT_SCHEMA_VERSION
    )


def artifact_entry_timing_is_valid(payload: dict[str, Any]) -> bool:
    """Whether the artifact records a strict, forward T+1-style entry date.

    This reader has no trading-calendar substrate, so it cannot prove that no
    intermediate session exists. It can still reject malformed dates and prove
    the producer-recorded entry is later than its as-of session; the producer
    owns the exact next-session lookup against qlib's calendar.
    """
    def strict_day(value: Any) -> date | None:
        if not isinstance(value, str) or len(value) != 10:
            return None
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.isoformat() == value else None

    as_of = strict_day(payload.get("as_of_date"))
    entry = strict_day(payload.get("entry_date"))
    return as_of is not None and entry is not None and entry > as_of

# Where the daily_recommend CLI writes its dated artifacts
# (RecommendationConfig.out_dir default "output/daily_recommend").
RECOMMEND_OUT_DIRNAME = "daily_recommend"

# The banner contract fields (工单 §2 / spec v2-daily-decision-page: model
# identity = model_path + model_type). Missing ANY of them renders a prominent
# WARN — never a default, placeholder or inferred value (the suspended-guard
# failure class this page exists to prevent).
BANNER_FIELDS: tuple[str, ...] = (
    "fit_end_for_inference",
    "train_window",
    "promoted_at",
    "model_path",
    "model_type",
)

# Display-only cost reference: ONE full round trip at the CERTIFIED
# production cost convention. NOT a backtest input — a per-row visual
# anchor comparing the predicted score against a realistic in-and-out
# cost.
#
# Derived, not restated: the previous 30 bps literal predated the csi800
# N5 certification (20 bps one-way conservative) and understated the real
# cost by roughly half, so every row's anchor was optimistic.
#
# Sources of the three components:
#   * slippage — imported live from the certified guard profile
#     (``scripts/eval_profiles.py`` is a deliberately qlib-free module,
#     built so pins can read campaign semantics without dragging qlib
#     onto the import path).
#   * commission / stamp tax — market-wide canonical values that live in
#     ``PipelineConfig.commission_rate`` (the dataclass default both
#     engines share) and
#     ``src/core/canonical_backtest_contract.CN_STAMP_TAX_SCHEDULE_DEFAULT``.
#     Those are DELIBERATELY duplicated here rather than imported: the
#     contract module pulls qlib into the process, and this is a
#     production-facing read-only page. Consistency is pinned by
#     ``tests/logic/test_daily_decision_page_source.py`` — the same
#     "duplicate + pin, never import across the layer" treatment
#     ``web/operator_ui/update_status.py`` gives the writer's constants.
#
# Assembly mirrors ``src/core/backtest_runner.py``'s exchange kwargs:
#   open  = commission + slippage
#   close = commission + stamp tax + slippage
#: 认证口径的单边滑点。公开导出——页面文案要引用同一个数,写死
#: 「20 bps」会在 profile 挪动时和列名/被减数对不上(codex #443 r1)。
CERTIFIED_SLIPPAGE_BPS = float(EVAL_PROFILES["csi800_n5"]["slippage_bps"])
_CERTIFIED_SLIPPAGE_BPS = CERTIFIED_SLIPPAGE_BPS  # 既有内部引用
_COMMISSION_RATE = 0.0005
_STAMP_TAX_BPS = 5.0

_OPEN_COST = _COMMISSION_RATE + _CERTIFIED_SLIPPAGE_BPS / 1e4
_CLOSE_COST = (
    _COMMISSION_RATE + _STAMP_TAX_BPS / 1e4 + _CERTIFIED_SLIPPAGE_BPS / 1e4
)
ROUND_TRIP_COST = _OPEN_COST + _CLOSE_COST



def model_meta_paths(model_path: str) -> tuple[Path, Path]:
    """Candidate meta sidecars, PRIORITY ORDER — promotion meta first.

    Mirrors the CLI's ``scripts/daily_recommend._model_meta_paths`` convention
    (the source of truth for the two sidecar names):
    1. ``<stem>.meta.json``      — hand-curated PROMOTION meta (banner source)
    2. ``<model>.pkl.meta.json`` — ModelTrainer sidecar (carries pkl_sha256)

    PRECONDITION: ``model_path`` names a model. A blank path has no sidecars
    to name and raises here rather than inventing a pair rooted at the
    working directory — callers that can see a blank value handle it before
    asking (see :func:`load_promotion_meta`).
    """
    if not model_path.strip():
        raise ValueError(
            "model_path 为空,没有可命名的 meta 旁文件——调用方应先判空,"
            "不要在此臆造一对指向工作目录的路径。")
    p = Path(model_path)
    return (p.with_suffix(".meta.json"), p.with_name(p.name + ".meta.json"))


def _read_json_file(path: Path) -> dict[str, Any] | None:
    """Best-effort local JSON read: None on missing/unreadable/non-dict."""
    if not path.is_file():
        return None
    try:
        loaded: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def load_promotion_meta(model_path: str) -> dict[str, Any] | None:
    """The PROMOTION meta (``<stem>.meta.json``) — and ONLY that — or None.

    The banner's source of truth is the promotion sidecar (spec
    v2-daily-decision-page). Deliberately NO fall-through to the trainer
    sidecar: if the promotion meta is missing/unreadable, the banner must
    report it missing loudly — a trainer sidecar that happens to carry some
    banner-shaped keys must not mask the absent promotion record (codex P2 on
    #330). The trainer sidecar is consumed separately, for the sha cross-check
    only (:func:`load_trainer_sidecar_sha`).
    """
    if not model_path.strip() or unusable_path_reason(model_path):
        # No model path configured at all — or one this host cannot resolve
        # to a single location. Reading sidecars beside it would answer about
        # `<cwd>/D:/prod/…`, i.e. about a different artifact than the command
        # names (codex #431 r34, same rule the manifest read got in r31). `model_meta_paths("")` would raise
        # (``Path("").with_suffix`` → "empty name") and take the page down
        # with a traceback; this contract is best-effort-or-None, and "there
        # is no model to read a sidecar beside" is exactly None. Reachable
        # since r24 made the resolver stop substituting the default for an
        # empty QUANT_MODEL_PATH.
        return None
    promotion_sidecar = model_meta_paths(model_path)[0]
    return _read_json_file(promotion_sidecar)


def load_trainer_sidecar_sha(model_path: str) -> str | None:
    """``pkl_sha256`` from the ModelTrainer sidecar (cross-check source)."""
    if not model_path.strip() or unusable_path_reason(model_path):
        return None                      # see load_promotion_meta (r24/r34)
    trainer_sidecar = model_meta_paths(model_path)[1]
    meta = _read_json_file(trainer_sidecar)
    if meta is None:
        return None
    sha = meta.get("pkl_sha256")
    return str(sha) if isinstance(sha, str) and sha else None


def banner_status(
    promo_meta: dict[str, Any] | None,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """(present banner values, missing field names) — absent meta = all missing."""
    if promo_meta is None:
        return {}, BANNER_FIELDS
    present: dict[str, Any] = {}
    missing: list[str] = []
    for field_name in BANNER_FIELDS:
        value = promo_meta.get(field_name)
        if value is None or value == "" or value == []:
            missing.append(field_name)
        else:
            present[field_name] = value
    return present, tuple(missing)


def list_recommendation_artifacts(
    root: Path | None = None,
) -> tuple[tuple[str, Path], ...]:
    """Dated recommendation JSONs as (date, path), newest first."""
    base = root if root is not None else output_path(RECOMMEND_OUT_DIRNAME)
    if not base.is_dir():
        return ()
    found: list[tuple[str, Path]] = []
    for child in base.iterdir():
        artifact_date = recommendation_artifact_date(child.name)
        if artifact_date is not None and child.is_file():
            found.append((artifact_date, child))
    found.sort(key=lambda item: item[0], reverse=True)
    return tuple(found)


@dataclass(frozen=True)
class ArtifactMetaStatus:
    """Cross-check of a selected artifact against the current model."""

    artifact_is_v1: bool          # TRUE legacy: no version marker, no meta
    # v2-marked file whose meta is missing/non-dict: the producer contract
    # ALWAYS writes a dict meta for v2, so this is a CORRUPT/incompatible
    # artifact — it must not be soft-labelled as an expected legacy file
    # (codex P2 on #330).
    artifact_is_corrupt_v2: bool
    artifact_model_sha: str | None
    current_model_sha: str | None
    # True = mismatch (WARN: generated by a different model);
    # False = match; None = not comparable (v1 artifact or missing sha).
    sha_mismatch: bool | None
    # Ensemble artifact (meta carries an "ensemble" block, PR-A' of
    # csi800-n5-production-promotion): its identity is the manifest
    # sha256, NOT a single-pickle sha — comparing it against the trainer
    # sidecar's pkl_sha256 is a category error, so sha_mismatch stays
    # None and the page renders a dedicated ensemble-identity notice
    # instead of a false "other model" warning (codex #390 r3).
    artifact_is_ensemble: bool = False
    artifact_ensemble_sha: str | None = None


def artifact_meta_status(
    payload: dict[str, Any], current_model_sha: str | None,
) -> ArtifactMetaStatus:
    meta = payload.get("meta")
    if not isinstance(meta, dict):
        has_version_marker = "artifact_schema_version" in payload
        return ArtifactMetaStatus(
            artifact_is_v1=not has_version_marker,
            artifact_is_corrupt_v2=has_version_marker,
            artifact_model_sha=None,
            current_model_sha=current_model_sha,
            sha_mismatch=None,
        )
    if "ensemble" in meta:
        # Ensemble artifact: identity = manifest sha256 (the producer
        # omits model_pkl_sha256 — that field is reserved for the
        # single-pickle digest, codex #390 r3). The single-model
        # sidecar cross-check does not apply; None mismatch + the
        # dedicated flag lets the page say so honestly. PRESENCE of the
        # key is what marks the artifact ensemble-shaped (codex #390
        # r5): a non-dict value is a MALFORMED ensemble block, not a
        # single-pickle artifact — it must not fall back into the
        # single-model identity namespace.
        ensemble_block = meta.get("ensemble")
        ens_sha_raw = (
            ensemble_block.get("manifest_sha256")
            if isinstance(ensemble_block, dict)
            else None
        )
        return ArtifactMetaStatus(
            artifact_is_v1=False,
            artifact_is_corrupt_v2=False,
            artifact_model_sha=None,
            current_model_sha=current_model_sha,
            sha_mismatch=None,
            artifact_is_ensemble=True,
            artifact_ensemble_sha=(
                str(ens_sha_raw)
                if isinstance(ens_sha_raw, str) and ens_sha_raw
                else None
            ),
        )
    artifact_sha_raw = meta.get("model_pkl_sha256")
    artifact_sha = (
        str(artifact_sha_raw)
        if isinstance(artifact_sha_raw, str) and artifact_sha_raw
        else None
    )
    mismatch: bool | None
    if artifact_sha is None or current_model_sha is None:
        mismatch = None
    else:
        mismatch = artifact_sha != current_model_sha
    return ArtifactMetaStatus(
        artifact_is_v1=False,
        artifact_is_corrupt_v2=False,
        artifact_model_sha=artifact_sha,
        current_model_sha=current_model_sha,
        sha_mismatch=mismatch,
    )


def journal_model_id(payload: dict[str, Any]) -> str:
    """The model identity a journal entry records for this artifact.

    Prefers the artifact meta's pkl sha (binds the decision to the exact
    model); an honest sentinel for v1 artifacts — never a fabricated id.
    """
    meta = payload.get("meta")
    if isinstance(meta, dict):
        # Ensemble artifact first (codex #390 r3): its content-bound
        # identity is the manifest sha256; the prefix keeps it from
        # ever being confused with a single-pickle digest. An ensemble
        # block WITHOUT that sha never falls through to the
        # model_pkl_sha256 branch (codex #390 r4): a malformed/hand-
        # edited artifact carrying both would re-enter the single-
        # pickle namespace this path exists to avoid — the honest
        # fallback is the path identity, then a dedicated sentinel.
        # Key PRESENCE marks the artifact ensemble-shaped (codex #390
        # r5): a non-dict block is malformed-ensemble, never a
        # single-pickle artifact.
        if "ensemble" in meta:
            ensemble_block = meta.get("ensemble")
            if isinstance(ensemble_block, dict):
                ens_sha = ensemble_block.get("manifest_sha256")
                if isinstance(ens_sha, str) and ens_sha:
                    return f"ensemble:{ens_sha}"
            path = meta.get("model_path")
            if isinstance(path, str) and path:
                return path
            return "unknown(malformed-ensemble-artifact)"
        sha = meta.get("model_pkl_sha256")
        if isinstance(sha, str) and sha:
            return sha
        path = meta.get("model_path")
        if isinstance(path, str) and path:
            return path
    return "unknown(v1-artifact)"


#: 候选表里成本参照列的表头。由常量派生——列名与所减的数字
#: 永远同源(旧代码把 30 写死在表头,口径一改就自相矛盾)。
COST_REFERENCE_COLUMN = f"评分−{ROUND_TRIP_COST * 1e4:.0f}bps(往返成本参照)"


def cost_reference(score: float) -> float:
    """score − one certified round trip (display-only column).

    Read it as a CONSERVATIVE LOWER BOUND, not a per-day hurdle: the
    score is a 1-day predicted return (``label_horizon_days=1``) while
    production holds a week (N5), so one round trip is amortized over
    ~5 days rather than paid daily.
    """
    return score - ROUND_TRIP_COST


def picks_table_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Pass-through candidate rows + the cost-reference column.

    Renders EXACTLY the generation-side fields (rank/code/name/score/
    tradable_flag/unavailable_reason — the reason already carries st /
    suspension / one-price-lock); the ONLY computed column is the
    display-only cost reference. No UI-side flag recomputation (工单 §1.4).
    """
    rows: list[dict[str, Any]] = []
    picks = payload.get("picks")
    if not isinstance(picks, list):
        # The producer contract ALWAYS writes picks as a list (write_outputs);
        # a missing/non-list value is a corrupt or incompatible artifact.
        # Masquerading it as "empty buy list" would hide the corruption from
        # the operator (codex P2 on #330) — fail loud instead. An EMPTY list
        # remains the legitimate empty state.
        raise ValueError(
            "工件形状违约:picks 缺失或不是列表(生产端 write_outputs 恒写 "
            f"list)。该文件可能损坏或非推荐工件;实际类型:{type(picks).__name__}。"
        )
    for pick in picks:
        if not isinstance(pick, dict):
            raise ValueError(
                "工件形状违约:picks 内含非 object 项"
                f"(类型 {type(pick).__name__})——文件可能损坏。"
            )
        score = pick.get("predicted_score")
        score_val = float(score) if isinstance(score, (int, float)) else None
        rows.append({
            "rank": pick.get("rank"),
            "代码": pick.get("stock_code"),
            "名称": pick.get("stock_name"),
            "评分": score_val,
            COST_REFERENCE_COLUMN: (
                cost_reference(score_val) if score_val is not None else None
            ),
            "可交易": pick.get("tradable_flag"),
            "不可用原因": pick.get("unavailable_reason"),
        })
    return rows


@dataclass(frozen=True)
class HoldState:
    """Cadence-aware HOLD verdict for one artifact (PR-A of
    2026-07-20-csi800-n5-production-promotion, codex #385 r5).

    ``is_hold`` is True ONLY for an explicit ``rebalance_day: false``
    payload; a missing field (legacy daily artifact) or ``true`` renders
    exactly as before — backward compatible by construction. A present
    field with a non-bool value is a shape violation (producer writes a
    real bool) and is surfaced as ``malformed`` so the page can refuse
    loudly instead of guessing.
    """

    is_hold: bool
    next_rebalance_date: str | None
    malformed: str | None


def hold_state(payload: dict[str, Any]) -> HoldState:
    """Read the cadence fields off a recommendation artifact payload."""
    # ABSENT field = legacy daily artifact (backward compatible).
    # A PRESENT null is NOT legacy (codex #386 r1): the producer writes
    # a real bool or omits the key entirely — a null here is a shape
    # violation and must not silently downgrade to daily semantics.
    if "rebalance_day" not in payload:
        return HoldState(is_hold=False, next_rebalance_date=None,
                         malformed=None)
    raw = payload["rebalance_day"]
    if not isinstance(raw, bool):
        return HoldState(
            is_hold=False, next_rebalance_date=None,
            malformed=(
                "工件形状违约:rebalance_day 存在但不是布尔值"
                f"(实际类型 {type(raw).__name__})——文件可能损坏。"
            ),
        )
    nxt = payload.get("next_rebalance_date")
    nxt_str = str(nxt) if isinstance(nxt, str) and nxt else None
    return HoldState(is_hold=(raw is False), next_rebalance_date=nxt_str,
                     malformed=None)


# ---------------------------------------------------------------------------
# Provenance verdict — the incumbent × artifact matrix as DATA, not as a
# chain of elifs (codex #430 r1..r4 leaked four different cells of it: a
# single-model artifact under an ensemble incumbent, a v1 artifact called
# "single-model shaped", an unset pointer read as "single", and an
# unresolvable incumbent falling through to the retired model's sidecar).
# Ordered branches give no structural guarantee that every combination is
# covered; a table does, and the test asserts the table is total.
#
# Coverage is necessary, not sufficient: r5 found a cell that WAS covered and
# answered too weakly. Hence the explicit precedence rule in
# ``classify_provenance`` — definite refusals outrank unknowns — instead of
# whichever order the branches happened to end up in.
# ---------------------------------------------------------------------------

# Artifact shapes, as classified by ``artifact_meta_status``.
ARTIFACT_KINDS: tuple[str, ...] = (
    "ensemble",          # v2, meta.ensemble carries manifest_sha256
    "ensemble_no_sha",   # v2, marked ensemble but manifest_sha256 missing
    "v1",                # no meta block at all — provenance unknown
    "single",            # v2, self-describing single-model
)
INCUMBENT_KINDS: tuple[str, ...] = ("ensemble", "single", "unresolvable")

# The SHAPE each side declares, independent of whether its identity can be
# bound. ``None`` = the shape itself is unknown, so no comparison is possible.
# An artifact missing ``manifest_sha256`` has still DECLARED itself ensemble
# (the ``meta.ensemble`` block is there) — losing the identity does not lose
# the shape (codex #430 r5).
_ARTIFACT_SHAPE: dict[str, str | None] = {
    "ensemble": "ensemble",
    "ensemble_no_sha": "ensemble",
    "v1": None,
    "single": "single",
}
_INCUMBENT_SHAPE: dict[str, str | None] = {
    "ensemble": "ensemble",
    "single": "single",
    "unresolvable": None,
}

# Verdicts the page renders. Each is one message; none means "say nothing".
VERDICT_MATCHES_INCUMBENT = "matches_incumbent"
VERDICT_OTHER_MANIFEST = "other_manifest"
VERDICT_ENSEMBLE_SHA_MISSING = "ensemble_sha_missing"
VERDICT_SHAPE_SINGLE_UNDER_ENSEMBLE = "shape_single_under_ensemble"
VERDICT_ENSEMBLE_UNDER_SINGLE = "ensemble_under_single"
VERDICT_V1_UNKNOWN = "v1_unknown_provenance"
VERDICT_INCUMBENT_UNRESOLVED = "incumbent_unresolved"
VERDICT_SINGLE_SHA_MISMATCH = "single_sha_mismatch"
VERDICT_SINGLE_SHA_UNKNOWN = "single_sha_unknown"
VERDICT_SINGLE_SHA_OK = "single_sha_ok"


def provenance_is_verified(verdict: str) -> bool:
    """Whether an artifact's source is confirmed for review projections."""
    return verdict in {VERDICT_MATCHES_INCUMBENT, VERDICT_SINGLE_SHA_OK}


def review_progress_is_available(*, verdict: str, artifact_contract_valid: bool) -> bool:
    """Whether provenance and artifact shape both support review projection."""
    return artifact_contract_valid and provenance_is_verified(verdict)


def classify_provenance(
    *,
    incumbent_kind: str,
    artifact_kind: str,
    ensemble_sha_matches: bool | None = None,
    single_sha_mismatch: bool | None = None,
) -> str:
    """Which provenance statement is TRUE for this (incumbent, artifact) pair.

    Pure and total over ``INCUMBENT_KINDS × ARTIFACT_KINDS`` — an unknown
    combination raises rather than silently returning "nothing to say",
    because "no warning" is exactly how a non-incumbent artifact gets
    presented as safe.

    The rule the four steps below encode: **a shape mismatch is the only
    DEFINITE refusal derivable without any identity at all, so it outranks
    every kind of "unknown".** Ordering it after the unknowns is what made
    ``single`` × ``ensemble_no_sha`` under-warn — a provably-non-incumbent
    artifact got the mild "identity unbindable" notice instead of "请勿据此
    下单" (codex #430 r5).
    """
    if incumbent_kind not in INCUMBENT_KINDS:
        raise ValueError(f"unknown incumbent kind: {incumbent_kind!r}")
    if artifact_kind not in ARTIFACT_KINDS:
        raise ValueError(f"unknown artifact kind: {artifact_kind!r}")

    # 1. Both shapes known and different → provably not the incumbent's
    #    output. No identity needed, and no later "unknown" can soften it.
    art_shape = _ARTIFACT_SHAPE[artifact_kind]
    inc_shape = _INCUMBENT_SHAPE[incumbent_kind]
    if art_shape is not None and inc_shape is not None and art_shape != inc_shape:
        return (VERDICT_SHAPE_SINGLE_UNDER_ENSEMBLE if art_shape == "single"
                else VERDICT_ENSEMBLE_UNDER_SINGLE)

    # 2. v1 carries no meta at all — even its SHAPE is unknown, so there was
    #    nothing to compare above and nothing to compare below.
    if artifact_kind == "v1":
        return VERDICT_V1_UNKNOWN

    # 3. Shape agrees (or the incumbent's shape is unknown) but the artifact
    #    declares no identity. Unbindable whatever production serves. When the
    #    incumbent is ALSO unresolvable this deliberately reports the
    #    artifact-side defect rather than repeating the incumbent one — the
    #    page banner already renders a prominent st.error for that.
    if artifact_kind == "ensemble_no_sha":
        return VERDICT_ENSEMBLE_SHA_MISSING

    # 4. An incumbent we could not confirm cannot vouch for — or against —
    #    anything. It must never fall through to the retired model's sidecar.
    if incumbent_kind == "unresolvable":
        return VERDICT_INCUMBENT_UNRESOLVED

    # 5. Shapes agree and both sides are identifiable — down to the digests.
    if incumbent_kind == "ensemble":
        return (VERDICT_MATCHES_INCUMBENT if ensemble_sha_matches
                else VERDICT_OTHER_MANIFEST)
    if single_sha_mismatch is True:
        return VERDICT_SINGLE_SHA_MISMATCH
    if single_sha_mismatch is None:
        return VERDICT_SINGLE_SHA_UNKNOWN
    return VERDICT_SINGLE_SHA_OK


def artifact_kind_of(status: ArtifactMetaStatus) -> str:
    """Map the meta-status flags onto exactly one ``ARTIFACT_KINDS`` value."""
    if status.artifact_is_ensemble:
        return "ensemble" if status.artifact_ensemble_sha else "ensemble_no_sha"
    if status.artifact_is_v1:
        return "v1"
    return "single"


def provenance_verdict(
    incumbent: IncumbentIdentity, status: ArtifactMetaStatus,
) -> str:
    """The verdict for a resolved incumbent and a selected artifact.

    The wiring lives here rather than in the page because a source-level
    pin cannot tell ``incumbent_kind=incumbent.kind`` from a plausible
    miswiring like ``"ensemble" if incumbent.is_ensemble else "single"`` —
    which silently collapses ``unresolvable`` into ``single`` and revives
    the r4 failure (the page then compares against the RETIRED model and
    says nothing when the digests happen to agree). As a function it is
    driven by real ``IncumbentIdentity``/``ArtifactMetaStatus`` values in
    the tests, so a miswiring fails behaviourally instead of surviving
    because the page still contains the right words somewhere.
    """
    art_sha = str(status.artifact_ensemble_sha or "")
    inc_sha = str(incumbent.manifest_sha256 or "")
    return classify_provenance(
        incumbent_kind=incumbent.kind,
        artifact_kind=artifact_kind_of(status),
        # An incumbent with no digest can confirm nothing — never let two
        # empty strings compare equal into a green "与现任一致".
        #
        # Belt-and-braces today: ``artifact_kind_of`` routes every
        # digest-less ensemble artifact to ``ensemble_no_sha``, which the
        # matrix answers before any comparison, so ``art_sha`` is non-empty
        # by the time this is read. That invariant is what makes the guard
        # redundant, so it is pinned directly
        # (test_a_bindable_digest_is_a_precondition_of_the_comparison) —
        # break the routing and this guard becomes load-bearing again.
        ensemble_sha_matches=bool(inc_sha) and art_sha == inc_sha,
        single_sha_mismatch=status.sha_mismatch,
    )
