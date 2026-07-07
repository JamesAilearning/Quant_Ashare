"""Pure helpers for the 今日推荐 (daily decision) page.

No Streamlit imports here — everything is unit-testable plain Python
(the P1-1 pages pattern: ``pages/_*_helpers.py`` pure + thin render page).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from web.operator_ui._path_guard import output_path

# Where the daily_recommend CLI writes its dated artifacts
# (RecommendationConfig.out_dir default "output/daily_recommend").
RECOMMEND_OUT_DIRNAME = "daily_recommend"

_ARTIFACT_RE = re.compile(r"daily_recommendation_(\d{4}-\d{2}-\d{2})\.json")

# The production model the banner describes. Mirrors the CLI default
# (scripts/daily_recommend._DEFAULT_MODEL) and docs/operations-env-vars.md.
ENV_MODEL_PATH = "QUANT_MODEL_PATH"
DEFAULT_MODEL_PATH = "D:/stock/phase_b_artifacts/alpha158_lgb_pit.pkl"

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

# Display-only cost reference: 30 bps round-trip (工单 §2, James' decision).
# NOT a backtest input — a per-row visual anchor comparing the predicted
# score against a realistic in-and-out cost.
ROUND_TRIP_COST = 0.0030


def resolve_model_path() -> str:
    """The production model path: env override > documented default."""
    return os.environ.get(ENV_MODEL_PATH, "").strip() or DEFAULT_MODEL_PATH


def model_meta_paths(model_path: str) -> tuple[Path, Path]:
    """Candidate meta sidecars, PRIORITY ORDER — promotion meta first.

    Mirrors the CLI's ``scripts/daily_recommend._model_meta_paths`` convention
    (the source of truth for the two sidecar names):
    1. ``<stem>.meta.json``      — hand-curated PROMOTION meta (banner source)
    2. ``<model>.pkl.meta.json`` — ModelTrainer sidecar (carries pkl_sha256)
    """
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
    promotion_sidecar = model_meta_paths(model_path)[0]
    return _read_json_file(promotion_sidecar)


def load_trainer_sidecar_sha(model_path: str) -> str | None:
    """``pkl_sha256`` from the ModelTrainer sidecar (cross-check source)."""
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
        match = _ARTIFACT_RE.fullmatch(child.name)
        if match and child.is_file():
            found.append((match.group(1), child))
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
        sha = meta.get("model_pkl_sha256")
        if isinstance(sha, str) and sha:
            return sha
        path = meta.get("model_path")
        if isinstance(path, str) and path:
            return path
    return "unknown(v1-artifact)"


def cost_reference(score: float) -> float:
    """score − 30 bps round-trip (display-only column)."""
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
            "评分−30bps(往返成本参照)": (
                cost_reference(score_val) if score_val is not None else None
            ),
            "可交易": pick.get("tradable_flag"),
            "不可用原因": pick.get("unavailable_reason"),
        })
    return rows
