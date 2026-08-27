"""Pure helpers for the 日度信号与人工决策 (daily decision) page.

No Streamlit imports here — everything is unit-testable plain Python
(the P1-1 pages pattern: ``pages/_*_helpers.py`` pure + thin render page).
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Sequence
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
        try:
            score_val = (
                float(score) if isinstance(score, (int, float)) else None)
        except OverflowError as exc:
            # JSON 的任意精度大整数（10**1000）让 float() 溢出——逃出去会
            # 崩掉每个消费方（工作台合成 + 日度决策详情页，codex #468）。
            # 数值超出 float 域的分是产出器产不出的（打分本就是 float）：
            # 按本函数既有契约折成 ValueError，读侧统一走「候选列表不合法」。
            raise ValueError(
                "工件形状违约:predicted_score 数值超出 float 表示域"
                "——文件可能损坏。"
            ) from exc
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
# 产出器形状合约 —— **一处实现，两处消费**
#
# 这一串闸原本只长在今日工作台（``summarise_daily_signal``）里。决策履历的
# 回溯扫描要读同一批工件的**同两件事**（节奏答案 + 清单），一旦它自己另写
# 一份「差几道闸」的校验，就成了同一份工件的第二条**更弱**的路径——工作台
# 会拒的损坏工件，在履历里被当成可信基准。codex 在 #475 一次点出两道
# （节奏双字段不全、重复 stock_code）；照着补那两道，剩下的六道就是下一轮
# 的同类漏洞。所以整类**下沉**到这里，两边调同一个函数。
#
# 刻意**不**含来源判据（``provenance_verdict``）：那道闸问的是「这份工件是
# 不是**现任**模型出的」。对历史基准而言，一次更早的再平衡本就可能出自上一
# 代模型——拿现任身份去拒它是错的。形状合约与身份归属是两个问题。
# ---------------------------------------------------------------------------


def pick_row_violation(pick: dict[str, Any]) -> str | None:
    """一行候选违约在哪——None = 合约内。

    钉的是产出器 `RecommendationPick`（frozen dataclass）的**全部**六键与
    类型（穷尽式，不挑其中几个——挑选就是下一个漏洞的形状）：rank int /
    stock_code 非空 str / stock_name str / predicted_score 数值 /
    tradable_flag bool / unavailable_reason str。
    """
    code = pick.get("stock_code")
    if not (isinstance(code, str) and code.strip()):
        return "stock_code 缺失或为空"
    if not isinstance(pick.get("stock_name"), str):
        return "stock_name 缺失或非字符串"
    rank = pick.get("rank")
    if isinstance(rank, bool) or not isinstance(rank, int):
        return "rank 缺失或非整数"
    score = pick.get("predicted_score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return "predicted_score 缺失或非有限数值"
    # NaN/inf 也拒：json.loads 接受裸 NaN，而 NaN 的比较恒 False 会悄悄
    # 穿过降序检查；产出器打分后 dropna 再构造 picks，非有限分产不出
    # （codex P2）。isfinite 对 JSON 任意精度大整数（10**1000）抛
    # OverflowError——检查自己不许成为崩溃源（codex 续指），同判非有限。
    try:
        finite = math.isfinite(score)
    except OverflowError:
        finite = False
    if not finite:
        return "predicted_score 缺失或非有限数值"
    # 不止验类型，验**字面**：产出器只落已过可交易筛选的行（untradable 在
    # 构造前被过滤，构造器写死 True/""——src/inference/daily_recommend 的
    # _build_picks）。False/非空 reason 的行产出器产不出；只验布尔会让
    # 「工件自己标注不可交易」的行照样计入候选数（codex P2）。
    if pick.get("tradable_flag") is not True:
        return "tradable_flag 缺失或非 True（产出器只落可交易行）"
    if pick.get("unavailable_reason") != "":
        return "unavailable_reason 非空串（产出器对入选行恒写空串）"
    return None


def producer_shape_violation(
    payload: dict[str, Any], *, as_of_date: str, entry_date: str,
) -> str | None:
    """这份 payload 的**形状**违约在哪——``None`` = 产出器产得出的形态。

    调用方须已确认：``payload`` 是 dict、``as_of_date`` 与文件名一致、
    ``entry_date`` 非空、schema 版本受支持、meta 不是 corrupt-v2。本函数
    从**清单**与**节奏**两组字段往下验，两者正是读侧真正据以下结论的东西。
    """
    try:
        # The detailed page treats a missing/non-list picks value, or a
        # non-object member, as a corrupt producer artifact. Every reader
        # must use that same boundary before presenting this file.
        picks_table_rows(payload)
    except ValueError as exc:
        return f"工件候选列表不合法：{exc}"
    # 行级契约：产出器 RecommendationPick（frozen dataclass）六键六型**恒写**
    # ——`picks: [{}]` 这类行数不出任何可买标的，却会把基数抬成 1、让最显
    # 眼的卡说「有再平衡指令 · 1 只候选」（codex P2）。详情页的 display 层
    # 刻意 pass-through（工单 §1.4）不动；驱动指令句的**基数**在此验约，
    # 违约=需核查，不做静默缩数。
    for index, pick in enumerate(payload["picks"]):
        problem = pick_row_violation(pick)
        if problem is not None:
            return (
                f"工件候选第 {index + 1} 行违约：{problem}（产出器恒写六键，"
                "缺任一即非产出器产物）。")
    # 清单级验约：同一 stock_code 出现两次是产出器产不出的形态——上游
    # _scores_to_inst_map 的 unique-instruments 守卫在构造 picks 之前就
    # fail-loud（其 docstring 明言）。逐行验约看不见跨行重复，基数会把
    # 「两行一只标的」报成「2 只候选」（codex P2）。
    codes = [str(pick["stock_code"]) for pick in payload["picks"]]
    if len(codes) != len(set(codes)):
        duplicated = sorted({c for c in codes if codes.count(c) > 1})
        return (
            f"工件候选包含重复代码 {duplicated}——产出器上游对重复标的 "
            "fail-loud，产不出这种清单；需核查。")
    # 序与秩验约（canonical 契约 v2-daily-stock-recommendation：Ranks
    # SHALL be contiguous 1..N，按 predicted_score 降序稳定排序）——
    # [2] 或 [1,1] 这类断秩/乱序清单产出器产不出，基数照数会拿损坏工件
    # 驱动头卡（codex P2）。
    # topk 界（canonical 契约 N ≤ topk；产出器在 meta 无条件写 topk）：
    # 缺失/非法/被超出都是产出器产不出的形态——超长清单照数会把损坏工件
    # 的基数端上头卡（codex P2）。
    meta_for_topk = payload.get("meta")
    raw_topk = (meta_for_topk.get("topk")
                if isinstance(meta_for_topk, dict) else None)
    if (isinstance(raw_topk, bool) or not isinstance(raw_topk, int)
            or raw_topk < 0):
        return (
            f"工件 meta.topk 缺失或非法（实际 {raw_topk!r}）——产出器无条件"
            "写非负 int；需核查。")
    if len(payload["picks"]) > raw_topk:
        return (
            f"工件候选 {len(payload['picks'])} 条超出 meta.topk"
            f"（{raw_topk}）——canonical 契约 N ≤ topk，产出器产不出；需核查。")
    ranks = [pick["rank"] for pick in payload["picks"]]
    if ranks != list(range(1, len(ranks) + 1)):
        return (
            f"工件候选 rank 序列 {ranks} 不是连续 1..N——canonical 契约"
            "明文 contiguous，产出器产不出；需核查。")
    scores = [pick["predicted_score"] for pick in payload["picks"]]
    if any(scores[i] < scores[i + 1] for i in range(len(scores) - 1)):
        return (
            "工件候选 predicted_score 非降序——canonical 契约按分降序稳定"
            "排序，产出器产不出；需核查。")

    cadence = hold_state(payload)
    if cadence.malformed is not None:
        return cadence.malformed
    # 节奏日期验约（codex P2）：产出器只写严格 ISO 日期或 null（日历尾附
    # 近合法 None）——hold_state 刻意宽容（非 str 静默成 None、非 ISO 原样
    # 保留），把 `123`/"tomorrow" 这类产出器产不出的值放到头卡上宣布
    # 「HOLD 无需动作」是拿损坏工件下结论。缺键 = cadence-1 合法形态。
    # 节奏双字段 both-or-neither（write_outputs 在同一个守卫块里同写两键；
    # codex P2）：只带其一是产出器产不出的形态——缺 next 键时 hold_state
    # 会静默补 None，头卡把损坏工件当已核验 HOLD 报「未记录」。显式 null
    # （日历尾外无锚）与缺键是两回事。
    if ("rebalance_day" in payload) != ("next_rebalance_date" in payload):
        present = ("rebalance_day" if "rebalance_day" in payload
                   else "next_rebalance_date")
        return (
            f"工件只带节奏双字段之一（{present}）——产出器在同一守卫块同写"
            "两键，产不出这种形态；需核查。")
    if "next_rebalance_date" in payload:
        raw_next = payload["next_rebalance_date"]
        next_problem: str | None = None
        if payload.get("rebalance_day") is True and raw_next != str(as_of_date):
            # 跨字段不变式（无需日历复推，codex P2）：next_rebalance_date(d)
            # 在 d 本身是再平衡日时**必然返回 d**——rebalance_day=true 配
            # null 或别的日期是产出器产不出的节奏记录。
            next_problem = (
                f"再平衡日的 next 必为 as_of（{as_of_date}）——实际 "
                f"{raw_next!r}，产出器产不出")
        elif raw_next is not None:
            if not isinstance(raw_next, str):
                next_problem = f"非 str/null（实际 {type(raw_next).__name__}）"
            else:
                try:
                    strict = date.fromisoformat(raw_next).isoformat() == raw_next
                except ValueError:
                    strict = False
                if not strict:
                    next_problem = f"不是严格 ISO 日期（实际 {raw_next!r}）"
                elif (payload.get("rebalance_day") is False
                        and (raw_next < str(entry_date)
                             or date.fromisoformat(raw_next).weekday() >= 5)):
                    # 产出器契约：next_rebalance_date(d) = 首个再平衡日
                    # >= d，HOLD 日的 as_of 本身不是再平衡日 → 严格大于。
                    # 过去/当日值是产出器产不出的——头卡把它宣布成「下一
                    # 再平衡日」是拿损坏工件报日程（codex P2）。再平衡日
                    # 工件的 next == as_of 合法，不在此限。
                    next_problem = (
                        f"不可能的取值（{raw_next}）：HOLD 日的下一再平衡"
                        f"日是交易日且最早为 entry（{entry_date}）——早于"
                        " entry 或落在周末的值产出器产不出")
        if next_problem is not None:
            return (
                f"工件 next_rebalance_date {next_problem}——产出器只写严格 "
                "ISO 或 null，需核查。")
    return None


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


# ---------------------------------------------------------------------------
# 名义持仓的基准：最近一次「再平衡日」工件
#
# 生产是 csi800 / N5 / 周频 iso_week —— 大多数交易日是 HOLD 日，出单工件写
# ``rebalance_day: false``。所以「我此刻名义上跟的是哪一天的那张单」这个问题，
# 答案通常**不是今天**，而要往回找到最近一个再平衡日。
#
# 这一页此前只能一次看一天（日期下拉框只给日期、不标哪天是再平衡日），要回答
# 这个问题得逐个日期点开、逐个看 HOLD 横幅。下面这组纯函数把那次回溯做成一次
# 可复核的搜索：找到了就说是哪一天，找不到就说**沿途每一份工件各自因为什么被
# 跳过**——「没有基准」和「基准在 30 天前」对操作人的下一步完全不同。
#
# 边界（本模块的既有纪律，这里逐条继承）：
# * 不重推产出器的节奏语义：只读它写下的 ``rebalance_day``，绝不自己按日历算
#   哪天该再平衡（``src.inference.rebalance_schedule`` 是产出器那一侧的东西，
#   把它引进 web/ 会跨越现有 import 边界）。
# * 不做 I/O：读盘由调用方注入，页面注入的是过了 ``guard_output_path`` 的那个
#   读取器。这样这组函数保持可单测，而读边界仍由页面那一侧执法。
# * 不推断缺失：老工件没有 cadence 字段时**不**假设它是再平衡日——那等于替一次
#   没记录节奏的运行编造语义。
# ---------------------------------------------------------------------------

#: 回溯为什么在某一份工件上**停下**。除了 HOLD 日，其余每一种都表示
#: 「这一份回答不了它是不是再平衡日」——而那正是不能再往回翻的理由。
BASELINE_SKIP_HOLD = "hold_day"
BASELINE_BLOCK_NO_CADENCE = "no_cadence"
BASELINE_BLOCK_MALFORMED_CADENCE = "malformed_cadence"
BASELINE_BLOCK_UNSUPPORTED_SCHEMA = "unsupported_schema"
BASELINE_BLOCK_ENTRY_TIMING = "entry_timing"
BASELINE_BLOCK_DATE_MISMATCH = "date_mismatch"
BASELINE_BLOCK_CORRUPT_V2 = "corrupt_v2"
BASELINE_BLOCK_UNREADABLE = "unreadable"
#: 工件形状违约（清单或节奏字段不是产出器产得出的形态）。
BASELINE_BLOCK_SHAPE = "shape_violation"

#: 向后兼容的别名（首版把这些都叫 SKIP，语义已改为 BLOCK）。
BASELINE_SKIP_NO_CADENCE = BASELINE_BLOCK_NO_CADENCE
BASELINE_SKIP_MALFORMED_CADENCE = BASELINE_BLOCK_MALFORMED_CADENCE
BASELINE_SKIP_UNSUPPORTED_SCHEMA = BASELINE_BLOCK_UNSUPPORTED_SCHEMA
BASELINE_SKIP_ENTRY_TIMING = BASELINE_BLOCK_ENTRY_TIMING
BASELINE_SKIP_UNREADABLE = BASELINE_BLOCK_UNREADABLE

#: 回溯的硬上界。模块的读侧纪律拒绝「无上界地扩大读取直到找到」——一次翻遍
#: 全部历史工件既慢又会把「基准早已过期」这个事实说成「找到了」。
DEFAULT_BASELINE_SCAN_LIMIT: Final[int] = 60


@dataclass(frozen=True)
class SkippedCandidate:
    """回溯途中经过的一份工件，以及它对回溯意味着什么。"""

    trade_date: str
    reason: str
    detail: str


@dataclass(frozen=True)
class NominalBaselineSearch:
    """向后找「最近一次再平衡日工件」的结果。

    三种终局，对操作人的下一步各不相同：

    * ``found``：``baseline_date`` 非空——那一天的清单就是名义持仓；
    * **不可知**（``blocked_by`` 非 ``None``）：回溯在某一份工件上停下，因为
      那一份**回答不了**它自己是不是再平衡日。它可能正是一次比更早那份更近
      的再平衡——所以继续往回翻会把一份**过期**的清单报成当前基准。这不是
      「没有基准」，是「不知道」；
    * ``exhausted`` / ``limit_reached``：一路都是**经过校验**的 HOLD 日，翻
      到底/翻到上限也没遇到再平衡日。

    只有**经过校验的 HOLD** 才许可继续往回翻：它是唯一能证明「那天没换手、
    所以更早那张单仍然有效」的证据。读不出来、schema 不认、日期对不上、
    meta 损坏、没有节奏字段——每一种都让这条证据链断在这里。
    """

    baseline_date: str
    baseline_payload: dict[str, Any]
    skipped: tuple[SkippedCandidate, ...]
    scanned: int
    limit_reached: bool
    exhausted: bool
    #: 让回溯停下的那一份（``None`` = 证据链没断）。
    blocked_by: SkippedCandidate | None = None

    @property
    def found(self) -> bool:
        return bool(self.baseline_date)

    @property
    def unknowable(self) -> bool:
        """证据链断了——既不是「找到了」，也不是「确实没有」。"""
        return self.blocked_by is not None


def _baseline_block(
    payload: dict[str, Any], artifact_date: str,
) -> SkippedCandidate | None:
    """这份 payload 能不能被信任到「说得出它是不是再平衡日」。

    这一串闸与**选中工件流**（``daily_decision.py`` 的日期一致性闸与
    corrupt-v2 闸）逐条对齐。回溯若只做其中一部分，就成了同一份工件的第二条
    **更弱**的校验路径——那正是「一份页面自己会 stop 的工件，在这里被当成
    可信基准」的由来。
    """

    if not artifact_schema_is_supported(payload):
        return SkippedCandidate(
            artifact_date, BASELINE_BLOCK_UNSUPPORTED_SCHEMA,
            "工件 schema 版本不是本页支持的那一版，其字段语义无法确认。",
        )
    # 文件名日期 ↔ payload as_of_date：改名/拷贝过的工件会让「八月三日那一
    # 份」其实装着八月十日的截面——按它当基准就是把未来数据当成当日应持有。
    payload_as_of = str(payload.get("as_of_date", ""))
    if payload_as_of != artifact_date:
        return SkippedCandidate(
            artifact_date, BASELINE_BLOCK_DATE_MISMATCH,
            f"文件名日期与 payload 的 as_of_date 不一致（payload 记的是 "
            f"{payload_as_of!r}）——文件可能被改名/拷贝或已损坏。",
        )
    if not artifact_entry_timing_is_valid(payload):
        return SkippedCandidate(
            artifact_date, BASELINE_BLOCK_ENTRY_TIMING,
            "工件的 as_of / entry 日期不构成严格向前的建仓时点。",
        )
    # 带 v2 标记却没有 dict meta = 损坏（产出器对 v2 恒写 dict meta）。选中
    # 工件流对同一形状是 st.stop()，这里不能更宽松。
    if artifact_meta_status(payload, None).artifact_is_corrupt_v2:
        return SkippedCandidate(
            artifact_date, BASELINE_BLOCK_CORRUPT_V2,
            "带 artifact_schema_version 标记但 meta 块缺失/非 object——"
            "文件可能损坏或非本系统产物。",
        )
    # 形状合约与今日工作台**同一个函数**（``producer_shape_violation``）。
    # 回溯据以下结论的正是清单与节奏这两组字段——少验一道，这里就成了同一
    # 份工件的第二条更弱路径：工作台会判「需核查」的工件，在履历里被当成
    # 可信基准端给操作人。节奏双字段只带其一、重复 stock_code（codex #475
    # 两条）都在这一道闸里，且不止这两条。
    # entry 必是严格 ISO：上面的 ``artifact_entry_timing_is_valid`` 已经证过
    # （缺失/空串/非 ISO 都在那道闸被拦下），所以这里不再补一道恒真的检查。
    entry_date = str(payload["entry_date"])
    # 节奏字段自身的形状先判——它有自己的成因码（回溯停在「说不出自己是不
    # 是再平衡日」上，与「清单不可信」是两回事，页面分开说）。
    state = hold_state(payload)
    if state.malformed:
        return SkippedCandidate(
            artifact_date, BASELINE_BLOCK_MALFORMED_CADENCE, state.malformed)
    shape_problem = producer_shape_violation(
        payload, as_of_date=artifact_date, entry_date=entry_date)
    if shape_problem is not None:
        return SkippedCandidate(
            artifact_date, BASELINE_BLOCK_SHAPE, shape_problem)
    if "rebalance_day" not in payload:
        # 老工件没有节奏语义。**不**当作再平衡日（那等于替一次没记录节奏的
        # 运行编造语义），也**不**当作 HOLD 继续往回翻——它可能本身就是一次
        # 再平衡，只是那时还没有这个字段。两种猜测都不做，停在这里说不知道。
        return SkippedCandidate(
            artifact_date, BASELINE_BLOCK_NO_CADENCE,
            "工件没有记录 rebalance_day（早于节奏语义），无法确认它是不是"
            "再平衡日。",
        )
    return None


def find_nominal_baseline(
    artifacts: Sequence[tuple[str, Path]],
    *,
    read_payload: Callable[[Path], dict[str, Any] | None],
    as_of: str = "",
    limit: int = DEFAULT_BASELINE_SCAN_LIMIT,
) -> NominalBaselineSearch:
    """从 ``as_of`` 起向后找**第一份**可信的再平衡日工件。

    ``artifacts`` 是 :func:`list_recommendation_artifacts` 的产出（日期倒序）。
    ``read_payload`` 读一份工件，读不出来返回 ``None``——由调用方注入，因为读盘
    要过页面那一侧的输出目录守卫。``as_of`` 为空表示从最新的一份开始。

    **只有经过校验的 HOLD 日才许可继续往回翻。** 任何一份「回答不了自己是不是
    再平衡日」的工件（读不出来 / schema 不认 / 文件名与 payload 日期对不上 /
    meta 损坏 / 没有节奏字段 / 节奏字段形状违约）都让回溯**就地停下**并判为
    不可知——继续翻过去，就可能把一份**已被它取代**的更早清单报成当前基准，
    而那正是「拿过期的单当此刻该持有的」。
    """

    skipped: list[SkippedCandidate] = []
    scanned = 0
    limit_reached = False
    for artifact_date, path in artifacts:
        if as_of and artifact_date > as_of:
            continue
        if scanned >= limit:
            limit_reached = True
            break
        scanned += 1
        payload = read_payload(path)
        if payload is None:
            blocked = SkippedCandidate(
                artifact_date, BASELINE_BLOCK_UNREADABLE,
                "工件读不出来（缺失、损坏、或不在允许的输出目录内）——"
                "它本身可能就是一次更近的再平衡，所以更早那份不能当基准。",
            )
            return NominalBaselineSearch(
                baseline_date="", baseline_payload={}, skipped=tuple(skipped),
                scanned=scanned, limit_reached=False, exhausted=False,
                blocked_by=blocked)
        block = _baseline_block(payload, artifact_date)
        if block is not None:
            return NominalBaselineSearch(
                baseline_date="", baseline_payload={}, skipped=tuple(skipped),
                scanned=scanned, limit_reached=False, exhausted=False,
                blocked_by=block)
        if not hold_state(payload).is_hold:
            return NominalBaselineSearch(
                baseline_date=artifact_date,
                baseline_payload=payload,
                skipped=tuple(skipped),
                scanned=scanned,
                limit_reached=False,
                exhausted=False,
            )
        skipped.append(SkippedCandidate(
            artifact_date, BASELINE_SKIP_HOLD, "该日是 HOLD 日，不换手。"))
    return NominalBaselineSearch(
        baseline_date="",
        baseline_payload={},
        skipped=tuple(skipped),
        scanned=scanned,
        limit_reached=limit_reached,
        exhausted=not limit_reached,
    )


def baseline_roster(payload: dict[str, Any]) -> tuple[str, ...]:
    """基准工件的**代码集合**（按工件里的顺序，即 rank 序）。

    刻意只给代码，不给任何数量口径：工件里只有 rank / predicted_score /
    tradable_flag，**没有权重、没有股数、没有金额**。把一个等权假设写进这里，
    就是凭空造出一份工件从未记录的仓位。

    代码缺失时**抛**，不静默丢弃那一条。``picks_table_rows`` 对
    ``stock_code`` 不做校验（``pick.get`` 缺失即 ``None``），而静默丢弃会让
    名单比工件的候选数**少一条却不说**——操作人看到「共 49 只」，无从知道
    第 50 条是被丢了还是本来就没有。
    """
    roster: list[str] = []
    for index, row in enumerate(picks_table_rows(payload)):
        code = row.get("代码")
        if not isinstance(code, str) or not code:
            raise ValueError(
                "工件形状违约:第 "
                f"{index + 1} 条候选缺少 stock_code(实际 {code!r})"
                "——文件可能损坏。"
            )
        roster.append(code)
    # 重复代码同样**抛**，不去重也不照数：这个元组的长度就是页面上那句
    # 「共 N 只」。``find_nominal_baseline`` 的形状闸通常先一步拦下，但本
    # 函数是公开入口——报数的那一处自己也得验，不能指望调用链上游都验过
    # （codex #475）。
    duplicated = sorted({c for c in roster if roster.count(c) > 1})
    if duplicated:
        raise ValueError(
            f"工件形状违约:候选包含重复代码 {duplicated}"
            "——产出器上游对重复标的 fail-loud,产不出这种清单。"
        )
    return tuple(roster)
