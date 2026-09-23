"""The operator must see the serving exception even when its scored count is zero."""

import json
from pathlib import Path

import pytest


def _payload():
    return {
        "meta": {"suspension_quarantine": {
            "policy_id": "suspend-688766-20251127-20251209", "instrument": "SH688766",
            "built_from_holey_fetch": True,
            "evidence": {
                "policy_id": "suspend-688766-20251127-20251209",
                "missing_dates": ["20251127"], "reference_sha256": "a" * 64,
                "retained_sha256": "b" * 64, "candidate_sha256": "c" * 64,
                "query_start_date": "20151001", "query_end_date": "20260922",
            },
        }},
        "n_quarantined": 0, "picks": [],
    }


def test_zero_scored_count_does_not_hide_active_security_isolation():
    from web.operator_ui.pages._suspension_quarantine import quarantine_notice

    notice = quarantine_notice(_payload())
    assert "688766" in notice
    assert "1 只" in notice
    assert "0" in notice
    assert "不完整" in notice
    assert "持仓" in notice


@pytest.mark.parametrize("mutation", ["count", "policy", "evidence", "leak", "missing_meta", "clean"])
def test_invalid_quarantine_disclosure_is_not_a_normal_signal(mutation):
    from web.operator_ui.pages._suspension_quarantine import quarantine_notice

    payload = _payload()
    if mutation == "count":
        payload["n_quarantined"] = True
    elif mutation == "policy":
        payload["meta"]["suspension_quarantine"]["policy_id"] = "unknown"
    elif mutation == "evidence":
        payload["meta"]["suspension_quarantine"]["evidence"]["missing_dates"] = ["20251210"]
    elif mutation == "leak":
        payload["picks"] = [{"stock_code": "688766.SH"}]
    elif mutation == "missing_meta":
        payload["meta"] = {}
    else:
        payload["meta"]["suspension_quarantine"]["built_from_holey_fetch"] = False
    with pytest.raises(ValueError, match="隔离"):
        quarantine_notice(payload)


def test_legacy_signal_does_not_gain_an_invented_isolation_state():
    from web.operator_ui.pages._suspension_quarantine import quarantine_notice

    assert quarantine_notice({"meta": {}, "picks": []}) is None


@pytest.mark.parametrize("code", [" SH688766 ", "688766.SH ", "", None, 688766])
def test_quarantine_signal_rejects_noncanonical_pick_identity(code):
    from web.operator_ui.pages._suspension_quarantine import quarantine_notice

    payload = _payload()
    payload["picks"] = [{"stock_code": code}]
    with pytest.raises(ValueError, match="隔离"):
        quarantine_notice(payload)


def test_both_pages_render_quarantine_warning_from_the_shared_contract():
    pages = Path(__file__).resolve().parents[2] / "web/operator_ui/pages"
    for page in ("daily_decision.py", "today_workbench.py"):
        source = (pages / page).read_text(encoding="utf-8")
        assert "quarantine_notice(" in source
        assert "st.warning(_quarantine_notice)" in source


def test_shared_artifact_gate_rejects_invalid_quarantine_disclosure():
    from web.operator_ui.pages._daily_decision_helpers import producer_shape_violation

    payload = json.loads(json.dumps(_payload()))
    payload["n_quarantined"] = -1
    # Quarantine validation precedes ordinary fields; malformed metadata is
    # never silently ignored merely because another reader needs fewer fields.
    assert "隔离" in producer_shape_violation(
        payload, as_of_date="2026-09-21", entry_date="2026-09-22")


def test_clean_current_signal_does_not_hide_quarantined_historical_baseline():
    from web.operator_ui.pages._suspension_quarantine import quarantine_notice

    assert quarantine_notice({"meta": {}, "picks": []}) is None
    assert "不完整" in quarantine_notice(_payload())
    page = Path(__file__).resolve().parents[2] / "web/operator_ui/pages/daily_decision.py"
    source = page.read_text(encoding="utf-8")
    assert "quarantine_notice(_baseline.baseline_payload)" in source
    assert 'st.warning(f"名义持仓基准工件：{_baseline_quarantine_notice}")' in source


@pytest.mark.parametrize("allow_holey", [False, True])
def test_cockpit_never_recommends_broad_override_for_quarantined_provider(tmp_path, allow_holey):
    from src.contracts.suspension_quarantine import SuspensionQuarantine
    from src.data.pit.bundle_integrity import write_bundle_integrity
    from src.data.tushare.fetch_types import FetchHole
    from web.operator_ui.pages._ops_cockpit_helpers import recommender_integrity_check

    evidence = SuspensionQuarantine.from_dict(_payload()["meta"]["suspension_quarantine"]["evidence"])
    write_bundle_integrity(tmp_path, built_from_holey_fetch=True, holes=(
        FetchHole("suspend_d", "file", "quarantined_history", 1, "known", quarantine=evidence),
    ))
    result = recommender_integrity_check(str(tmp_path), allow_holey=allow_holey)
    assert result.accepted is False
    assert result.holey is True
    assert "隔离" in result.reason
    assert "--suspension-quarantine" in result.reason
