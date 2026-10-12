"""Pure boundary tests; no model, provider, Tushare or external calendar."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime

import pytest

from src.contracts.recommendation_timing import (
    CN_TZ,
    candidate_is_current,
    validate_candidate_context,
)


def candidate() -> dict:
    row = {
        "as_of_date": "2026-10-09", "entry_date": "2026-10-12", "rank": 1,
        "stock_code": "SH600000", "stock_name": "示例", "predicted_score": 0.2,
        "eligible_as_of": True, "tradable_flag": None, "unavailable_reason": "",
    }
    return {
        "artifact_schema_version": 3, "recommendation_mode": "next_session_candidate",
        "entry_tradability_status": "unconfirmed", "as_of_date": "2026-10-09",
        "entry_date": "2026-10-12", "n_scored": 1, "n_masked": 0,
        "n_st_excluded": 0, "n_quarantined": 0, "picks": [row],
        "meta": {
            "generated_at": "2026-10-12T10:00:00+08:00", "topk": 50,
            "model_path": "synthetic.pkl", "model_pkl_sha256": "b" * 64,
            "provider_uri": "synthetic_provider", "instruments": "csi800",
            "fit_start_for_inference": "2018-01-02",
            "fit_end_for_inference": "2024-12-18",
            "bundle_tag": None, "bundle_built_at": None,
            "latest_closed_session": "2026-10-09",
            "trading_calendar": {
                "path": "schedule.json", "sha256": "a" * 64,
                "source_api": "trade_cal", "exchange": "SSE",
                "query": {"start_date": "20260101", "end_date": "20261231",
                          "fields": "exchange,cal_date,is_open"},
                "fetched_at": "2026-10-11T09:00:00+08:00",
            },
        },
    }


def test_candidates_have_eligibility_but_no_future_tradability():
    payload = candidate()
    validate_candidate_context(payload, audit_rows=payload["picks"])
    assert payload["picks"][0]["eligible_as_of"] is True
    assert payload["picks"][0]["tradable_flag"] is None


@pytest.mark.parametrize("flag", [True, False, "", "unconfirmed", 0])
def test_neither_true_nor_false_can_replace_unknown_tradability(flag):
    payload = candidate()
    payload["picks"][0]["tradable_flag"] = flag
    with pytest.raises(ValueError, match="null"):
        validate_candidate_context(payload)


@pytest.mark.parametrize("score", [float("nan"), float("inf"), float("-inf"), True, "0.2", 10**1000])
def test_nonfinite_text_boolean_or_overflowing_scores_refuse(score):
    payload = candidate()
    payload["picks"][0]["predicted_score"] = score
    with pytest.raises(ValueError, match="finite"):
        validate_candidate_context(payload)


@pytest.mark.parametrize("key", [
    "artifact_schema_version", "recommendation_mode", "entry_tradability_status",
    "n_scored", "n_masked", "n_st_excluded", "n_quarantined", "meta", "picks",
])
def test_required_candidate_fields_never_default_to_legacy(key):
    payload = candidate()
    del payload[key]
    with pytest.raises(ValueError):
        validate_candidate_context(payload)


@pytest.mark.parametrize("key,value", [
    ("recommendation_mode", "historical_replay"), ("entry_tradability_status", "confirmed"),
    ("artifact_schema_version", True), ("n_quarantined", False),
])
def test_invalid_modes_versions_and_boolean_counts_refuse(key, value):
    payload = candidate()
    payload[key] = value
    with pytest.raises(ValueError):
        validate_candidate_context(payload)


def test_audit_counts_and_exact_topk_projection_are_checked():
    payload = candidate()
    audit = deepcopy(payload["picks"])
    excluded = {**audit[0], "stock_code": "SH600001", "predicted_score": 0.1,
                "eligible_as_of": False, "unavailable_reason": "st"}
    audit.append(excluded)
    payload["n_st_excluded"] = 1
    validate_candidate_context(payload, audit_rows=audit)
    payload["n_st_excluded"] = 0
    with pytest.raises(ValueError, match="counts"):
        validate_candidate_context(payload, audit_rows=audit)
    payload["n_st_excluded"] = 1
    payload["picks"][0]["stock_name"] = "changed"
    with pytest.raises(ValueError, match="projection"):
        validate_candidate_context(payload, audit_rows=audit)


@pytest.mark.parametrize("field", ["eligible_as_of", "tradable_flag"])
def test_audit_requires_explicit_eligibility_and_unknown_status(field):
    payload = candidate()
    audit = deepcopy(payload["picks"])
    del audit[0][field]
    with pytest.raises(ValueError):
        validate_candidate_context(payload, audit_rows=audit)


def test_empty_topk_is_valid_without_inventing_a_placeholder_pick():
    payload = candidate()
    audit = deepcopy(payload["picks"])
    payload["meta"]["topk"] = 0
    payload["picks"] = []
    validate_candidate_context(payload, audit_rows=audit)


@pytest.mark.parametrize("key,value", [
    ("sha256", "broken"), ("exchange", "SZSE"), ("source_api", "calendar"),
    ("fetched_at", "2026-10-13T10:00:00+08:00"),
    ("fetched_at", "2026-10-01T10:00:00+08:00"),
    ("fetched_at", "2026-10-11T10:00:00"),
])
def test_calendar_context_never_fabricates_provenance(key, value):
    payload = candidate()
    payload["meta"]["trading_calendar"][key] = value
    with pytest.raises(ValueError):
        validate_candidate_context(payload)


def test_current_candidate_expires_at_entry_session_cutoff():
    payload = candidate()
    assert candidate_is_current(payload, now=datetime(2026, 10, 12, 15, 29, 59, tzinfo=CN_TZ))
    assert not candidate_is_current(payload, now=datetime(2026, 10, 12, 15, 30, tzinfo=CN_TZ))


def test_historical_candidate_recalculation_cannot_be_current():
    payload = candidate()
    payload["meta"]["latest_closed_session"] = "2026-10-10"
    assert not candidate_is_current(payload, now=datetime(2026, 10, 12, 11, tzinfo=CN_TZ))


def test_future_generation_or_naive_clock_cannot_be_current():
    payload = candidate()
    assert not candidate_is_current(payload, now=datetime(2026, 10, 12, 9, tzinfo=CN_TZ))
    with pytest.raises(ValueError, match="aware"):
        candidate_is_current(payload, now=datetime(2026, 10, 12, 11))


def test_a_preclose_bar_is_not_a_closed_decision_session():
    payload = candidate()
    payload["meta"]["latest_closed_session"] = "2026-10-12"
    with pytest.raises(ValueError, match="not closed"):
        validate_candidate_context(payload)


@pytest.mark.parametrize("key", [
    "model_path", "model_pkl_sha256", "provider_uri", "instruments",
    "fit_start_for_inference", "fit_end_for_inference", "bundle_tag", "bundle_built_at",
])
def test_v3_retains_required_generation_identity_without_defaults(key):
    payload = candidate()
    del payload["meta"][key]
    with pytest.raises(ValueError):
        validate_candidate_context(payload)


@pytest.mark.parametrize("key,value", [
    ("model_path", ""), ("provider_uri", None), ("instruments", []),
    ("fit_start_for_inference", 20180102), ("fit_end_for_inference", " "),
    ("model_pkl_sha256", "broken"), ("bundle_tag", False),
    ("bundle_built_at", {}),
])
def test_v3_rejects_malformed_existing_generation_fields(key, value):
    payload = candidate()
    payload["meta"][key] = value
    with pytest.raises(ValueError):
        validate_candidate_context(payload)


def ensemble_identity() -> dict:
    return {
        "schema_version": "csi800_n5_ensemble_manifest_v1", "manifest_path": "synthetic_manifest.json",
        "manifest_sha256": "c" * 64, "blend": "mean_skipna", "n_models": 3,
        "members": [
            {"pkl_path": f"synthetic_{index}.pkl", "pkl_sha256": character * 64,
             "fit_start": "2018-01-02", "fit_end": "2024-12-18"}
            for index, character in enumerate("def")
        ],
    }


def test_single_model_and_complete_ensemble_identities_are_exclusive():
    payload = candidate()
    payload["meta"]["ensemble"] = ensemble_identity()
    payload["meta"]["model_path"] = "synthetic_manifest.json"
    with pytest.raises(ValueError, match="identity"):
        validate_candidate_context(payload)
    del payload["meta"]["model_pkl_sha256"]
    validate_candidate_context(payload)


@pytest.mark.parametrize("key,value", [
    ("schema_version", True), ("schema_version", "unknown"),
    ("manifest_path", ""), ("manifest_sha256", "broken"),
    ("blend", None), ("n_models", False), ("n_models", 2), ("members", []),
])
def test_ensemble_context_requires_real_producer_shape(key, value):
    payload = candidate()
    del payload["meta"]["model_pkl_sha256"]
    payload["meta"]["ensemble"] = {**ensemble_identity(), key: value}
    payload["meta"]["model_path"] = "synthetic_manifest.json"
    with pytest.raises(ValueError):
        validate_candidate_context(payload)


@pytest.mark.parametrize("key", ["pkl_path", "pkl_sha256", "fit_start", "fit_end"])
def test_ensemble_members_cannot_lose_existing_identity_fields(key):
    payload = candidate()
    del payload["meta"]["model_pkl_sha256"]
    payload["meta"]["ensemble"] = ensemble_identity()
    payload["meta"]["model_path"] = "synthetic_manifest.json"
    del payload["meta"]["ensemble"]["members"][0][key]
    with pytest.raises(ValueError):
        validate_candidate_context(payload)


@pytest.mark.parametrize("count", [1, 2, 4])
def test_ensemble_identity_cannot_declare_a_consistent_non_three_member_shape(count):
    payload = candidate()
    del payload["meta"]["model_pkl_sha256"]
    ensemble = ensemble_identity()
    ensemble["n_models"] = count
    ensemble["members"] = (ensemble["members"] + [{
        "pkl_path": "synthetic_extra.pkl", "pkl_sha256": "0" * 64,
        "fit_start": "2018-01-02", "fit_end": "2024-12-18",
    }])[:count]
    payload["meta"].update(model_path=ensemble["manifest_path"], ensemble=ensemble)
    with pytest.raises(ValueError, match="three"):
        validate_candidate_context(payload)


@pytest.mark.parametrize("key", ["pkl_path", "pkl_sha256"])
def test_three_member_identity_cannot_repeat_a_path_or_content_digest(key):
    payload = candidate()
    del payload["meta"]["model_pkl_sha256"]
    ensemble = ensemble_identity()
    ensemble["members"][1][key] = ensemble["members"][0][key]
    payload["meta"].update(model_path=ensemble["manifest_path"], ensemble=ensemble)
    with pytest.raises(ValueError, match="distinct"):
        validate_candidate_context(payload)


def test_stamped_candidate_requires_its_existing_rebuild_nonce():
    payload = candidate()
    payload["meta"]["bundle_tag"] = "synthetic-tag"
    with pytest.raises(ValueError):
        validate_candidate_context(payload)


@pytest.mark.parametrize("fields", [
    {"rebalance_day": False}, {"next_rebalance_date": None},
    {"rebalance_day": None, "next_rebalance_date": None},
    {"rebalance_day": 0, "next_rebalance_date": None},
    {"rebalance_day": True, "next_rebalance_date": None},
    {"rebalance_day": False, "next_rebalance_date": "tomorrow"},
    {"rebalance_day": False, "next_rebalance_date": "2026-10-09"},
    {"rebalance_day": False, "next_rebalance_date": "2026-10-17"},
])
def test_candidate_cadence_refuses_incomplete_or_impossible_records(fields):
    payload = {**candidate(), **fields}
    with pytest.raises(ValueError):
        validate_candidate_context(payload)


@pytest.mark.parametrize("fields", [
    {}, {"rebalance_day": False, "next_rebalance_date": None},
    {"rebalance_day": False, "next_rebalance_date": "2026-10-12"},
    {"rebalance_day": True, "next_rebalance_date": "2026-10-09"},
])
def test_valid_cadence_remains_t_anchored_without_recomputing_its_schedule(fields):
    validate_candidate_context({**candidate(), **fields})
