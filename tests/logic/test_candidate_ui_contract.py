"""Candidate readers never certify future tradability or infer holdings."""

from __future__ import annotations

import hashlib
import os
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.contracts.recommendation_timing import CN_TZ
from web.operator_ui.incumbent import IncumbentIdentity
from web.operator_ui.pages import _daily_decision_helpers as decision
from web.operator_ui.pages._ops_cockpit_helpers import BundleFreshness
from web.operator_ui.pages._today_workbench_helpers import bind_current_signal, summarise_daily_signal
from web.operator_ui.recommendation_calendar import (
    MAX_CALENDAR_BYTES,
    TradingCalendarIdentity,
    read_trading_calendar_identity,
    trading_calendar_matches,
)


def legacy_payload() -> dict:
    return {
        "artifact_schema_version": 2,
        "as_of_date": "2026-10-08",
        "entry_date": "2026-10-09",
        "rebalance_day": True,
        "next_rebalance_date": "2026-10-08",
        "n_scored": 1,
        "n_masked": 0,
        "n_st_excluded": 0,
        "meta": {"ensemble": {"manifest_sha256": "manifest"},
                 "topk": 50, "instruments": "csi800"},
        "picks": [{"as_of_date": "2026-10-08", "entry_date": "2026-10-09",
                   "rank": 1, "stock_code": "SH600000", "stock_name": "示例",
                   "predicted_score": 0.1, "tradable_flag": True,
                   "unavailable_reason": ""}],
    }


def candidate_payload() -> dict:
    payload = legacy_payload()
    payload.update(artifact_schema_version=3,
                   recommendation_mode="next_session_candidate",
                   entry_tradability_status="unconfirmed", n_quarantined=0)
    payload["meta"].update(
        generated_at="2026-10-08T16:00:00+08:00",
        latest_closed_session="2026-10-08",
        model_path=str(Path.cwd() / "synthetic-manifest.json"),
        fit_start_for_inference="2024-01-01",
        fit_end_for_inference="2025-12-31",
        provider_uri=str(Path.cwd() / "synthetic-provider"),
        bundle_tag="tag", bundle_built_at="2026-10-08T15:45:00+08:00",
        ensemble={
            "schema_version": "csi800_n5_ensemble_manifest_v1",
            "manifest_path": str(Path.cwd() / "synthetic-manifest.json"),
            "manifest_sha256": "b" * 64,
            "blend": "mean_skipna", "n_models": 3,
            "members": [{
                "pkl_path": str(Path.cwd() / f"synthetic-member-{index}.pkl"),
                "pkl_sha256": character * 64,
                "fit_start": "2024-01-01", "fit_end": "2025-12-31",
            } for index, character in enumerate("cde")],
        },
        trading_calendar={
            "path": str(Path.cwd() / "synthetic-calendar.json"),
            "sha256": "a" * 64, "source_api": "trade_cal", "exchange": "SSE",
            "query": {"start_date": "20260101", "end_date": "20261231",
                      "fields": "exchange,cal_date,is_open"},
            "fetched_at": "2026-10-08T15:00:00+08:00",
        },
    )
    payload["picks"][0].update(eligible_as_of=True, tradable_flag=None)
    return payload


def test_legacy_confirmation_is_historical_never_a_current_signal():
    summary = summarise_daily_signal(
        "2026-10-08", legacy_payload(),
        incumbent=IncumbentIdentity(kind="ensemble", manifest_sha256="manifest"),
        current_model_sha=None,
    )
    assert summary.kind == "historical"
    assert "历史" in summary.detail


def test_candidate_blocks_baseline_instead_of_promoting_or_skipping_it():
    blocked = decision._baseline_block(candidate_payload(), "2026-10-08")
    assert blocked is not None
    assert blocked.reason == "candidate_not_holding"


def test_candidate_table_discloses_eligibility_separately_from_unknown_tradability():
    row = decision.picks_table_rows(candidate_payload())[0]
    assert row["截至信号日资格"] is True
    assert row["交易时核验"] == "未确认"
    assert "可交易" not in row


def test_candidate_row_accepts_only_explicit_eligible_and_null_tradability():
    row = candidate_payload()["picks"][0]
    assert decision.pick_row_violation(row, schema_version=3) is None
    for value in (True, False, "", "unconfirmed"):
        broken = {**row, "tradable_flag": value}
        assert decision.pick_row_violation(broken, schema_version=3) is not None


def summarize(payload, now):
    return summarise_daily_signal(
        payload["as_of_date"], payload,
        incumbent=IncumbentIdentity(kind="ensemble", manifest_sha256="b" * 64),
        current_model_sha=None, now=now,
    )


@pytest.mark.parametrize("now,kind", [
    (datetime(2026, 10, 8, 16, tzinfo=CN_TZ), "rebalance"),
    (datetime(2026, 10, 9, 15, 29, 59, tzinfo=CN_TZ), "rebalance"),
    (datetime(2026, 10, 9, 15, 30, tzinfo=CN_TZ), "historical"),
    (datetime(2026, 10, 8, 15, 59, tzinfo=CN_TZ), "historical"),
])
def test_current_candidate_uses_shared_exact_session_expiry(now, kind):
    assert summarize(candidate_payload(), now).kind == kind


@pytest.mark.parametrize("corruption", [1, 2, 4, "pkl_path", "pkl_sha256"])
def test_unchanged_manifest_sha_cannot_make_invalid_member_identity_current(corruption):
    payload = candidate_payload()
    ensemble = payload["meta"]["ensemble"]
    if isinstance(corruption, int):
        ensemble["n_models"] = corruption
        ensemble["members"] = (ensemble["members"] + [{
            "pkl_path": "synthetic-extra.pkl", "pkl_sha256": "f" * 64,
            "fit_start": "2024-01-01", "fit_end": "2025-12-31",
        }])[:corruption]
    else:
        ensemble["members"][1][corruption] = ensemble["members"][0][corruption]
    assert summarize(payload, datetime(2026, 10, 8, 17, tzinfo=CN_TZ)).kind == "needs_verification"


@pytest.mark.parametrize("key", ["recommendation_mode", "entry_tradability_status", "n_quarantined"])
def test_missing_candidate_contract_never_falls_back_to_legacy(key):
    payload = candidate_payload()
    del payload[key]
    assert summarize(payload, datetime(2026, 10, 8, 17, tzinfo=CN_TZ)).kind == "needs_verification"


@pytest.mark.parametrize("field,value", [
    ("eligible_as_of", False), ("eligible_as_of", 1),
    ("tradable_flag", True), ("tradable_flag", False),
])
def test_candidate_picks_cannot_claim_confirmed_tradability_or_false_eligibility(field, value):
    payload = candidate_payload()
    payload["picks"][0][field] = value
    assert summarize(payload, datetime(2026, 10, 8, 17, tzinfo=CN_TZ)).kind == "needs_verification"


def test_candidate_stops_history_scan_before_older_v2_baseline():
    payload = candidate_payload()
    read = []
    def reader(path):
        read.append(path)
        return deepcopy(payload) if path.name == "new.json" else legacy_payload()
    result = decision.find_nominal_baseline(
        [("2026-10-08", Path("new.json")), ("2026-10-07", Path("old.json"))],
        read_payload=reader, as_of="2026-10-08",
    )
    assert not result.found
    assert result.blocked_by.reason == "candidate_not_holding"
    assert read == [Path("new.json")]


def test_v3_clean_zero_quarantine_does_not_weaken_legacy_contract():
    from web.operator_ui.pages._suspension_quarantine import quarantine_notice
    assert quarantine_notice(candidate_payload()) is None
    with pytest.raises(ValueError):
        quarantine_notice({**legacy_payload(), "n_quarantined": 0})


def test_calendar_identity_reads_only_registered_config_and_matches_same_buffer(tmp_path):
    calendar = tmp_path / "schedule.json"
    raw = b'{"synthetic": true}'
    calendar.write_bytes(raw)
    identity = read_trading_calendar_identity(str(calendar))
    assert identity.sha256 == hashlib.sha256(raw).hexdigest()
    payload = candidate_payload()
    payload["meta"]["trading_calendar"].update(path=str(calendar), sha256=identity.sha256)
    assert trading_calendar_matches(payload, identity)
    payload["meta"]["trading_calendar"]["path"] = str(tmp_path / "never-open-artifact-path")
    assert not trading_calendar_matches(payload, identity)
    payload["meta"]["trading_calendar"].update(path=str(calendar), sha256="b" * 64)
    assert not trading_calendar_matches(payload, identity)


@pytest.mark.parametrize("kind", ["missing", "directory", "empty", "oversize"])
def test_missing_or_nonregular_calendar_never_claims_current(tmp_path, kind):
    path = tmp_path / "calendar.json"
    if kind == "directory":
        path.mkdir()
    elif kind == "empty":
        path.write_bytes(b"")
    elif kind == "oversize":
        path.write_bytes(b" " * (MAX_CALENDAR_BYTES + 1))
    identity = read_trading_calendar_identity(str(path))
    assert identity.sha256 is None
    assert identity.problem
    assert not trading_calendar_matches(candidate_payload(), identity)


@pytest.mark.parametrize("phase", ["before", "opened", "finished", "after"])
def test_hard_linked_calendar_disables_current_candidate_even_when_bytes_match(tmp_path, monkeypatch, phase):
    path, alias = tmp_path / "calendar.json", tmp_path / "calendar-alias.json"
    raw = b'{"synthetic": true}'
    path.write_bytes(raw)
    payload = candidate_payload()
    payload["meta"]["trading_calendar"].update(path=str(path), sha256=hashlib.sha256(raw).hexdigest())
    assert trading_calendar_matches(payload, read_trading_calendar_identity(str(path)))
    if phase == "before":
        os.link(path, alias)
    elif phase in {"opened", "finished"}:
        original_fstat = os.fstat
        calls = 0

        def link_at_descriptor_check(descriptor):
            nonlocal calls
            calls += 1
            if calls == (1 if phase == "opened" else 2):
                os.link(path, alias)
            return original_fstat(descriptor)

        monkeypatch.setattr(os, "fstat", link_at_descriptor_check)
    else:
        original_lstat = Path.lstat
        calls = 0

        def link_at_final_path_check(self, *args, **kwargs):
            nonlocal calls
            if self == path:
                calls += 1
                if calls == 2:
                    os.link(path, alias)
            return original_lstat(self, *args, **kwargs)

        monkeypatch.setattr(Path, "lstat", link_at_final_path_check)

    identity = read_trading_calendar_identity(str(path))
    assert identity.path is None and identity.sha256 is None and identity.problem
    assert not trading_calendar_matches(payload, identity)
    now = datetime(2026, 10, 8, 17, tzinfo=CN_TZ)
    meta = payload["meta"]
    fresh = BundleFreshness(
        known=True, tail_date="2026-10-08", days_behind=0, max_age_days=14,
        headroom_days=14, refuses_today=False, integrity_accepted=True,
        provider_uri=meta["provider_uri"], identity_tag=meta["bundle_tag"], built_at=meta["bundle_built_at"],
    )
    signal = summarize(payload, now)
    assert signal.kind == "rebalance"
    bound, answer = bind_current_signal(signal, fresh, identity, now=now)
    assert bound.kind == "needs_verification"
    assert answer.state == "unanswerable"


@pytest.mark.parametrize("phase", ["before", "opened", "finished", "after"])
def test_reparse_calendar_metadata_is_unverifiable_at_every_read_boundary(tmp_path, monkeypatch, phase):
    path = tmp_path / "calendar.json"
    path.write_bytes(b'{"synthetic": true}')
    original_lstat, original_fstat = Path.lstat, os.fstat
    calls = 0

    def flagged(info):
        return SimpleNamespace(
            **{name: getattr(info, name) for name in (
                "st_mode", "st_nlink", "st_size", "st_dev", "st_ino", "st_mtime_ns",
            )}, st_file_attributes=0x400,
        )

    if phase in {"before", "after"}:
        def path_observation(self, *args, **kwargs):
            nonlocal calls
            info = original_lstat(self, *args, **kwargs)
            if self == path:
                calls += 1
                if calls == (1 if phase == "before" else 2):
                    return flagged(info)
            return info

        monkeypatch.setattr(Path, "lstat", path_observation)
    else:
        def descriptor_observation(descriptor):
            nonlocal calls
            calls += 1
            info = original_fstat(descriptor)
            return flagged(info) if calls == (1 if phase == "opened" else 2) else info

        monkeypatch.setattr(os, "fstat", descriptor_observation)

    identity = read_trading_calendar_identity(str(path))
    assert identity.path is None and identity.sha256 is None and identity.problem
    assert not trading_calendar_matches(candidate_payload(), identity)


@pytest.mark.parametrize("broken", ["missing", "wrong_sha", "wrong_path", "provider", "tail"])
def test_current_input_mismatch_disables_cards_queue_and_review_together(broken):
    payload = candidate_payload()
    now = datetime(2026, 10, 8, 17, tzinfo=CN_TZ)
    signal = summarize(payload, now)
    meta = payload["meta"]
    calendar = TradingCalendarIdentity(os.path.normcase(meta["trading_calendar"]["path"]), "a" * 64)
    if broken == "missing":
        calendar = TradingCalendarIdentity(None, None, "unreadable")
    elif broken == "wrong_sha":
        calendar = TradingCalendarIdentity(calendar.path, "b" * 64)
    elif broken == "wrong_path":
        calendar = TradingCalendarIdentity(calendar.path + ".new", calendar.sha256)
    freshness = BundleFreshness(
        known=True, tail_date="2026-10-09" if broken == "tail" else "2026-10-08",
        days_behind=0, max_age_days=14, headroom_days=14, refuses_today=False,
        integrity_accepted=True, provider_uri=meta["provider_uri"] + ("-other" if broken == "provider" else ""),
        identity_tag=meta["bundle_tag"], built_at=meta["bundle_built_at"],
    )
    bound, answer = bind_current_signal(signal, freshness, calendar, now=now)
    assert bound.kind not in {"rebalance", "daily", "hold"}
    assert answer.state in {"unanswerable", "no_instruction"}


def test_current_candidate_still_requires_human_entry_confirmation():
    payload = candidate_payload()
    now = datetime(2026, 10, 8, 17, tzinfo=CN_TZ)
    meta = payload["meta"]
    fresh = BundleFreshness(
        known=True, tail_date="2026-10-08", days_behind=0, max_age_days=14,
        headroom_days=14, refuses_today=False, integrity_accepted=True,
        provider_uri=meta["provider_uri"], identity_tag=meta["bundle_tag"],
        built_at=meta["bundle_built_at"],
    )
    identity = TradingCalendarIdentity(os.path.normcase(meta["trading_calendar"]["path"]), "a" * 64)
    bound, answer = bind_current_signal(summarize(payload, now), fresh, identity, now=now)
    assert bound.kind == "rebalance"
    assert answer.state == "rebalance"
    assert "交易资格未确认" in answer.detail
    assert "不是订单" in answer.detail
    assert payload["picks"][0]["tradable_flag"] is None


def test_calendar_comparator_does_not_follow_artifact_path(monkeypatch):
    payload = candidate_payload()
    identity = TradingCalendarIdentity(os.path.normcase(payload["meta"]["trading_calendar"]["path"]), "a" * 64)
    def no_io(*args, **kwargs):
        raise AssertionError("artifact path must never be opened")
    monkeypatch.setattr(Path, "open", no_io)
    assert trading_calendar_matches(payload, identity)
    payload["meta"]["trading_calendar"]["path"] = "private-file-that-was-never-configured"
    assert not trading_calendar_matches(payload, identity)


def test_both_pages_use_same_binding_before_review_controls():
    pages = Path(__file__).resolve().parents[2] / "web" / "operator_ui" / "pages"
    decision_source = (pages / "daily_decision.py").read_text(encoding="utf-8")
    workbench_source = (pages / "today_workbench.py").read_text(encoding="utf-8")
    for source in (decision_source, workbench_source):
        assert "bind_current_signal(" in source
        assert "read_trading_calendar_identity(resolve_trading_calendar())" in source
    form = decision_source.index("elif not _artifact_contract_valid:")
    assert form < decision_source.index('st.button("✍ 记录决策"')
    assert form < decision_source.index("_journal = read_journal()")
    assert workbench_source.index("signal, _answer = bind_current_signal(") < workbench_source.index("_render_signal_summary(signal)")
