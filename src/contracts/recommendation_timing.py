"""Qlib-free meaning and timing of schema-v3 recommendation candidates.

This contract does not certify execution, prices, model performance or a
calendar's authenticity. The data adapter binds the real schedule bytes;
the generator binds the model/provider and only reads prices through T.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Final

CN_TZ: Final = timezone(timedelta(hours=8))
SESSION_CLOSE: Final = time(15, 30)
SCHEMA_VERSION: Final[int] = 3
MODE: Final[str] = "next_session_candidate"
ENTRY_STATUS: Final[str] = "unconfirmed"
CALENDAR_MAX_AGE: Final = timedelta(days=7)


def _day(value: Any, name: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO date") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{name} must be an exact ISO date")
    return parsed


def aware_clock(value: Any) -> datetime:
    """Normalize an explicitly aware instant; never guess local timezone."""
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must be a timezone-aware datetime")
    return value.astimezone(CN_TZ)


def _instant(value: Any, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an aware ISO timestamp")
    try:
        return aware_clock(datetime.fromisoformat(value))
    except ValueError as exc:
        raise ValueError(f"{name} must be an aware ISO timestamp") from exc


def _count(value: Any, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _text(value: Any, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")


def _digest(value: Any, name: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(f"{name} must be a SHA-256 digest")


def _generation_identity(meta: Mapping[str, Any]) -> None:
    """Retain the existing producer's context, without loading its assets."""
    for key in ("model_path", "provider_uri", "instruments",
                "fit_start_for_inference", "fit_end_for_inference"):
        _text(meta.get(key), key)
    for key in ("bundle_tag", "bundle_built_at"):
        if key not in meta:
            raise ValueError(f"missing required generation field {key}")
        if meta[key] is not None:
            _text(meta[key], key)
    if meta["bundle_tag"] is not None and meta["bundle_built_at"] is None:
        raise ValueError("bundle_tag requires its bundle_built_at rebuild nonce")
    if ("model_pkl_sha256" in meta) == ("ensemble" in meta):
        raise ValueError("exactly one single-model or ensemble identity is required")
    if "model_pkl_sha256" in meta:
        _digest(meta["model_pkl_sha256"], "model_pkl_sha256")
        return
    ensemble = meta["ensemble"]
    if not isinstance(ensemble, Mapping):
        raise ValueError("ensemble identity must be an object")
    # These are the existing manifest wire identities, not a new model path
    # or a replacement for ensemble_serving's certification checks.
    if (ensemble.get("schema_version") != "csi800_n5_ensemble_manifest_v1"
            or ensemble.get("blend") != "mean_skipna"):
        raise ValueError("ensemble identity requires its known schema and blend")
    _text(ensemble.get("manifest_path"), "ensemble manifest_path")
    _digest(ensemble.get("manifest_sha256"), "ensemble manifest_sha256")
    if meta["model_path"] != ensemble["manifest_path"]:
        raise ValueError("ensemble model_path must identify its manifest")
    count = _count(ensemble.get("n_models"), "ensemble n_models")
    members = ensemble.get("members")
    if not isinstance(members, list) or count != 3 or len(members) != 3:
        raise ValueError("ensemble identity requires exactly three members")
    for member in members:
        if not isinstance(member, Mapping):
            raise ValueError("ensemble member identity must be an object")
        for key in ("pkl_path", "fit_start", "fit_end"):
            _text(member.get(key), f"ensemble member {key}")
        _digest(member.get("pkl_sha256"), "ensemble member pkl_sha256")
    if (len({member["pkl_path"] for member in members}) != 3
            or len({member["pkl_sha256"] for member in members}) != 3):
        raise ValueError("ensemble identity requires distinct member paths and digests")


def _cadence(payload: Mapping[str, Any], as_of: date, entry: date) -> None:
    """Validate recorded cadence only; never derive a second N5 schedule."""
    if ("rebalance_day" in payload) != ("next_rebalance_date" in payload):
        raise ValueError("cadence fields must be both present or both absent")
    if "rebalance_day" not in payload:
        return
    flag = payload["rebalance_day"]
    next_value = payload["next_rebalance_date"]
    if type(flag) is not bool:
        raise ValueError("rebalance_day must be a strict bool")
    if flag:
        if next_value != as_of.isoformat():
            raise ValueError("rebalance_day=True requires next_rebalance_date equal to T")
    elif next_value is not None:
        next_day = _day(next_value, "next_rebalance_date")
        if next_day < entry or next_day.weekday() >= 5:
            raise ValueError("HOLD next_rebalance_date must be a weekday no earlier than entry")


def _row(row: Any, as_of: str, entry: str, *, pick: bool) -> bool:
    if not isinstance(row, Mapping):
        raise ValueError("candidate row must be an object")
    if row.get("as_of_date") != as_of or row.get("entry_date") != entry:
        raise ValueError("candidate row dates disagree with the run")
    code = row.get("stock_code")
    if not isinstance(code, str) or not code or code != code.strip():
        raise ValueError("candidate row has an invalid security identity")
    if not isinstance(row.get("stock_name"), str):
        raise ValueError("candidate row stock_name must be text")
    score = row.get("predicted_score")
    if not isinstance(score, (int, float)) or type(score) not in (int, float):
        raise ValueError("candidate scores must be finite numbers")
    try:
        finite = math.isfinite(score)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError("candidate scores must be finite numbers")
    eligible = row.get("eligible_as_of")
    if type(eligible) is not bool:
        raise ValueError("candidate eligible_as_of must be a strict bool")
    if "tradable_flag" not in row or row["tradable_flag"] is not None:
        raise ValueError("entry tradability must be explicitly null, not true or false")
    reason = row.get("unavailable_reason")
    allowed = {"", "st", "data_quarantine", "suspended", "one_price_lock", "unavailable"}
    if not isinstance(reason, str) or reason not in allowed or eligible != (reason == ""):
        raise ValueError("candidate eligibility and exclusion reason disagree")
    if pick and not eligible:
        raise ValueError("an excluded security cannot be a candidate pick")
    return eligible


def validate_candidate_context(
    payload: Any, *, audit_rows: Sequence[Mapping[str, Any]] | None = None,
) -> None:
    """Reject malformed v3 before publication or current-candidate display.

    Readers without the full audit validate the self-contained pick/run
    boundary. The writer also passes all audit rows to reconcile counts and
    the exact stable Top-K projection. No field absence selects an old mode.
    """
    if not isinstance(payload, Mapping):
        raise ValueError("candidate artifact must be an object")
    if type(payload.get("artifact_schema_version")) is not int or payload["artifact_schema_version"] != SCHEMA_VERSION:
        raise ValueError("candidate artifact requires schema version 3")
    if payload.get("recommendation_mode") != MODE or payload.get("entry_tradability_status") != ENTRY_STATUS:
        raise ValueError("candidate artifact requires explicit mode and unconfirmed entry status")
    as_of = _day(payload.get("as_of_date"), "as_of_date")
    entry = _day(payload.get("entry_date"), "entry_date")
    if entry <= as_of:
        raise ValueError("planned entry must be strictly later than T")
    _cadence(payload, as_of, entry)
    meta = payload.get("meta")
    if not isinstance(meta, Mapping):
        raise ValueError("candidate artifact requires generation metadata")
    _generation_identity(meta)
    generated = _instant(meta.get("generated_at"), "generated_at")
    latest = _day(meta.get("latest_closed_session"), "latest_closed_session")
    if as_of > latest or datetime.combine(latest, SESSION_CLOSE, CN_TZ) > generated:
        raise ValueError("candidate decision session is not closed at generation")
    context = meta.get("trading_calendar")
    keys = {"path", "sha256", "source_api", "exchange", "query", "fetched_at"}
    if not isinstance(context, Mapping) or set(context) != keys:
        raise ValueError("candidate artifact requires the complete trading-calendar binding")
    if not isinstance(context["path"], str) or not context["path"].strip():
        raise ValueError("trading-calendar path is required")
    _digest(context["sha256"], "trading-calendar SHA-256")
    if context["source_api"] != "trade_cal" or context["exchange"] != "SSE":
        raise ValueError("trading-calendar source must be Tushare SSE trade_cal")
    query = context["query"]
    if not isinstance(query, Mapping) or set(query) != {"start_date", "end_date", "fields"}:
        raise ValueError("trading-calendar query binding is incomplete")
    bounds = []
    for key in ("start_date", "end_date"):
        value = query[key]
        if not isinstance(value, str) or re.fullmatch(r"[0-9]{8}", value) is None:
            raise ValueError("trading-calendar query dates must be YYYYMMDD")
        bounds.append(_day(f"{value[:4]}-{value[4:6]}-{value[6:]}", key))
    if (query["fields"] != "exchange,cal_date,is_open" or bounds[0] > as_of
            or bounds[1] < max(entry, generated.date()) or bounds[1] < bounds[0]):
        raise ValueError("trading-calendar query does not cover candidate timing")
    fetched = _instant(context["fetched_at"], "trading-calendar fetched_at")
    if fetched > generated or generated - fetched > CALENDAR_MAX_AGE:
        raise ValueError("trading-calendar binding is future or stale at generation")
    counts = {name: _count(payload.get(name), name) for name in (
        "n_scored", "n_masked", "n_st_excluded", "n_quarantined",
    )}
    topk = _count(meta.get("topk"), "topk")
    picks = payload.get("picks")
    if not isinstance(picks, list) or len(picks) != min(counts["n_scored"], topk):
        raise ValueError("candidate pick count disagrees with eligible pool/Top-K")
    codes = set()
    previous_score: int | float | None = None
    for rank, row in enumerate(picks, 1):
        _row(row, as_of.isoformat(), entry.isoformat(), pick=True)
        if type(row.get("rank")) is not int or row["rank"] != rank or row["stock_code"] in codes:
            raise ValueError("candidate picks require contiguous ranks and unique securities")
        score = row["predicted_score"]
        if previous_score is not None and score > previous_score:
            raise ValueError("candidate picks must be ranked by descending score")
        previous_score = score
        codes.add(row["stock_code"])
    if audit_rows is not None:
        expected = dict.fromkeys(counts, 0)
        audit_codes = set()
        eligible_rows = []
        previous_score = None
        for row in audit_rows:
            eligible = _row(row, as_of.isoformat(), entry.isoformat(), pick=False)
            if row["stock_code"] in audit_codes:
                raise ValueError("candidate audit has duplicate securities")
            audit_codes.add(row["stock_code"])
            score = row["predicted_score"]
            if previous_score is not None and score > previous_score:
                raise ValueError("candidate audit must retain descending stable score order")
            previous_score = score
            reason = row["unavailable_reason"]
            key = ("n_scored" if eligible else "n_quarantined" if reason == "data_quarantine"
                   else "n_st_excluded" if reason == "st" else "n_masked")
            expected[key] += 1
            if eligible:
                eligible_rows.append(row)
        if expected != counts:
            raise ValueError("candidate counts disagree with audited eligibility")
        for pick_row, audited in zip(picks, eligible_rows[:topk], strict=True):
            if any(pick_row[key] != audited[key] for key in (
                "stock_code", "stock_name", "predicted_score", "eligible_as_of",
                "tradable_flag", "unavailable_reason", "as_of_date", "entry_date",
            )):
                raise ValueError("candidate picks disagree with the audited Top-K projection")


def candidate_is_current(payload: Any, *, now: datetime) -> bool:
    """A bounded display claim, never a tradability or holdings verdict."""
    validate_candidate_context(payload)
    clock = aware_clock(now)
    meta = payload["meta"]
    return (
        payload["as_of_date"] == meta["latest_closed_session"]
        and _instant(meta["generated_at"], "generated_at") <= clock
        and clock - _instant(meta["trading_calendar"]["fetched_at"], "fetched_at") <= CALENDAR_MAX_AGE
        and clock < datetime.combine(_day(payload["entry_date"], "entry_date"), SESSION_CLOSE, CN_TZ)
    )
