"""Synthetic serving regressions for evidence-derived security isolation."""

from __future__ import annotations

import copy
import csv
import json
from dataclasses import replace
from datetime import date
from pathlib import Path
from unittest.mock import Mock

import pytest

from src.inference import daily_recommend as dr
from tests.logic.inference import test_suspension_quarantine_recommend as legacy

run_recommend = legacy.run_recommend

_POLICY = "suspend-security-anomalies-v1"
_CODES = ["300001.SZ", "600036.SH"]
_INSTRUMENTS = ["SZ300001", "SH600036"]
_CSV_CONTEXT = [
    "suspension_quarantine_policy", "quarantined_instruments",
    "built_from_holey_fetch", "n_quarantined",
]


def _evidence():
    # Independent literals: neither identities nor dates come from the policy.
    return {
        "policy_id": _POLICY,
        "missing_dates": ["20260115", "20260116"],
        "reference_sha256": "a" * 64,
        "retained_sha256": "b" * 64,
        "candidate_sha256": "c" * 64,
        "query_start_date": "20100101",
        "query_end_date": "20260922",
        "quarantined_ts_codes": list(_CODES),
        "history_sha256": [],
        "predecessor_sha256": None,
    }


def test_dynamic_two_securities_are_excluded_before_topk_and_disclosed(
    run_recommend, monkeypatch, tmp_path,
):
    monkeypatch.setattr(legacy, "_evidence", _evidence)
    scores = {"SZ300001": 0.99, "SH600036": 0.98, "SH688766": 0.8, "SH600000": 0.7}
    result = run_recommend(scores=scores, policy=_POLICY, topk=2)

    assert [pick.stock_code for pick in result.picks] == ["SH688766", "SH600000"]
    audit = result.scored_frame.set_index("stock_code")
    assert audit["predicted_score"].to_dict() == scores
    for instrument in _INSTRUMENTS:
        assert not audit.loc[instrument, "tradable_flag"]
        assert audit.loc[instrument, "unavailable_reason"] == "data_quarantine"
    assert audit.loc["SH688766", "tradable_flag"]
    assert (result.n_scored, result.n_masked, result.n_st_excluded, result.n_quarantined) == (2, 0, 0, 2)
    context = result.run_meta["suspension_quarantine"]
    assert context["instruments"] == _INSTRUMENTS
    assert context["evidence"] == _evidence()

    paths = dr.write_outputs(result, str(tmp_path / "dynamic"))
    payload = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
    assert payload["meta"]["suspension_quarantine"] == context
    assert payload["n_quarantined"] == 2
    assert dr._quarantine_output_context(result)["quarantined_instruments"] == ";".join(_INSTRUMENTS)
    for key in ("csv", "audit"):
        with Path(paths[key]).open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            assert reader.fieldnames[-4:] == _CSV_CONTEXT
            rows = list(reader)
        assert len(rows) == (2 if key == "csv" else 4)
        assert all(row["quarantined_instruments"] == "SZ300001;SH600036" for row in rows)
        assert all(row["n_quarantined"] == "2" for row in rows)


@pytest.fixture
def run_security_recommend(run_recommend, monkeypatch):
    monkeypatch.setattr(legacy, "_evidence", _evidence)

    def run(*, codes=None, **updates):
        evidence = _evidence()
        if codes is not None:
            evidence["quarantined_ts_codes"] = list(codes)
        monkeypatch.setattr(legacy, "_evidence", lambda: evidence)
        return run_recommend(policy=_POLICY, **updates)

    return run


@pytest.mark.parametrize("identities", [
    ("SZ300001", "SH600036"), ("300001.SZ", "600036.SH"), ("sz300001", "sh600036"),
])
def test_dynamic_exclusion_uses_existing_ticker_aliases_without_changing_scores(
    run_security_recommend, identities,
):
    scores = {identities[0]: 0.99, identities[1]: 0.98, "SH688766": 0.8, "SH600000": 0.7}
    result = run_security_recommend(scores=scores, topk=2)
    assert [pick.stock_code for pick in result.picks] == ["SH688766", "SH600000"]
    audit = result.scored_frame.set_index("stock_code")
    assert audit["predicted_score"].to_dict() == scores
    assert audit.loc[list(identities), "unavailable_reason"].tolist() == ["data_quarantine"] * 2
    assert result.n_quarantined == 2


def test_dynamic_list_is_not_the_legacy_two_security_incident(run_security_recommend):
    scores = {"SZ000001": 0.99, "SH688005": 0.98, "SH688766": 0.8, "SH600036": 0.7}
    result = run_security_recommend(
        codes=["000001.SZ", "688005.SH"], scores=scores, topk=2,
    )
    assert [pick.stock_code for pick in result.picks] == ["SH688766", "SH600036"]
    assert result.run_meta["suspension_quarantine"]["instruments"] == ["SZ000001", "SH688005"]
    assert result.n_quarantined == 2


def test_one_dynamic_security_keeps_plural_context_and_csv_projection(
    run_security_recommend, tmp_path,
):
    result = run_security_recommend(
        codes=["600036.SH"], scores={"SH600036": 0.99, "SH688766": 0.8},
    )
    context = result.run_meta["suspension_quarantine"]
    assert context["instruments"] == ["SH600036"]
    assert "instrument" not in context
    projection = dr._quarantine_output_context(result)
    assert projection["quarantined_instruments"] == "SH600036"
    assert "quarantined_instrument" not in projection
    paths = dr.write_outputs(result, str(tmp_path / "one_dynamic"))
    for key in ("csv", "audit"):
        with Path(paths[key]).open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            assert reader.fieldnames[-4:] == _CSV_CONTEXT
            assert all(row["quarantined_instruments"] == "SH600036" for row in reader)


@pytest.mark.parametrize("nan_affected", [False, True])
def test_dynamic_disclosure_survives_zero_scored_exclusions(
    run_security_recommend, tmp_path, nan_affected,
):
    scores = {"SH688766": 0.8}
    if nan_affected:
        scores.update({instrument: float("nan") for instrument in _INSTRUMENTS})
    result = run_security_recommend(scores=scores)
    assert result.n_quarantined == 0
    assert [pick.stock_code for pick in result.picks] == ["SH688766"]
    assert result.run_meta["suspension_quarantine"]["instruments"] == _INSTRUMENTS
    paths = dr.write_outputs(result, str(tmp_path / "zero_matches"))
    payload = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
    assert payload["n_quarantined"] == 0
    assert payload["meta"]["suspension_quarantine"]["evidence"] == _evidence()
    for key in ("csv", "audit"):
        with Path(paths[key]).open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        assert rows and all(row["quarantined_instruments"] == "SZ300001;SH600036" for row in rows)
        assert all(row["n_quarantined"] == "0" for row in rows)


def test_dynamic_empty_exports_keep_plural_headers_and_json_evidence(
    run_security_recommend, tmp_path,
):
    result = run_security_recommend(scores={"SH688766": 0.8})
    # Exercise a valid serialization-only empty boundary, not a fabricated
    # successful empty model inference.
    result = replace(result, picks=(), n_scored=0,
                     scored_frame=result.scored_frame.iloc[:0].copy())
    paths = dr.write_outputs(result, str(tmp_path / "empty"))
    payload = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
    assert payload["picks"] == [] and payload["n_quarantined"] == 0
    assert payload["meta"]["suspension_quarantine"]["evidence"] == _evidence()
    for key in ("csv", "audit"):
        with Path(paths[key]).open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            assert reader.fieldnames[-4:] == _CSV_CONTEXT
            assert list(reader) == []


def test_recognized_dynamic_selector_does_not_fabricate_quarantine_for_clean_bundle(
    run_security_recommend, tmp_path,
):
    result = run_security_recommend(clean=True, scores={"SH600036": 0.99, "SH688766": 0.8})
    assert result.n_quarantined == 0
    assert "suspension_quarantine" not in result.run_meta
    assert result.picks[0].stock_code == "SH600036"
    paths = dr.write_outputs(result, str(tmp_path / "clean"))
    payload = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
    assert "suspension_quarantine" not in payload["meta"]
    assert "n_quarantined" not in payload


@pytest.mark.parametrize("corruption", [
    "missing_code", "extra_code", "reordered_codes", "singular", "both_shapes",
    "inner_missing_code", "inner_extra_code", "inner_reordered", "inner_duplicate",
    "inner_tuple", "inner_null", "inner_invalid_code", "missing_history",
    "invalid_history", "no_evidence", "incomplete_false", "count", "no_context",
])
def test_forged_dynamic_list_refuses_before_creating_or_replacing_outputs(
    run_security_recommend, tmp_path, corruption,
):
    result = run_security_recommend(scores={
        "SZ300001": 0.99, "SH600036": 0.98, "SH688766": 0.8,
    })
    output = tmp_path / "existing"
    paths = dr.write_outputs(result, str(output))
    before = {key: Path(path).read_bytes() for key, path in paths.items()}
    meta = copy.deepcopy(result.run_meta)
    context = meta["suspension_quarantine"]
    evidence = context["evidence"]
    if corruption == "missing_code":
        context["instruments"] = ["SZ300001"]
    elif corruption == "extra_code":
        context["instruments"].append("SH688766")
    elif corruption == "reordered_codes":
        context["instruments"].reverse()
    elif corruption == "singular":
        del context["instruments"]
        context["instrument"] = "SZ300001"
    elif corruption == "both_shapes":
        context["instrument"] = "SZ300001"
    elif corruption == "inner_missing_code":
        evidence["quarantined_ts_codes"] = ["300001.SZ"]
    elif corruption == "inner_extra_code":
        evidence["quarantined_ts_codes"].append("688766.SH")
    elif corruption == "inner_reordered":
        evidence["quarantined_ts_codes"].reverse()
    elif corruption == "inner_duplicate":
        evidence["quarantined_ts_codes"] = ["300001.SZ", "300001.SZ"]
    elif corruption == "inner_tuple":
        evidence["quarantined_ts_codes"] = tuple(_CODES)
    elif corruption == "inner_null":
        evidence["quarantined_ts_codes"] = None
    elif corruption == "inner_invalid_code":
        evidence["quarantined_ts_codes"] = ["300001.SZ", "600036.sh"]
    elif corruption == "missing_history":
        del evidence["history_sha256"]
    elif corruption == "invalid_history":
        evidence["history_sha256"] = ["broken"]
    elif corruption == "no_evidence":
        context["evidence"] = None
    elif corruption == "incomplete_false":
        context["built_from_holey_fetch"] = False
    elif corruption == "count":
        result = replace(result, n_quarantined=1)
    else:
        del meta["suspension_quarantine"]
    forged = replace(result, run_meta=meta)
    with pytest.raises(dr.DailyRecommendationError, match="quarantine"):
        dr.write_outputs(forged, str(output))
    assert {key: Path(path).read_bytes() for key, path in paths.items()} == before
    absent = tmp_path / "not_created"
    with pytest.raises(dr.DailyRecommendationError, match="quarantine"):
        dr.write_outputs(forged, str(absent))
    assert not absent.exists()


@pytest.mark.parametrize("identity", [
    "SZ300001", "SH600036", "300001.SZ", "600036.SH", "sz300001", "sh600036",
])
def test_either_dynamic_security_leaking_into_picks_is_refused(
    run_security_recommend, tmp_path, identity,
):
    result = run_security_recommend(scores={
        "SZ300001": 0.99, "SH600036": 0.98, "SH688766": 0.8,
    })
    forged = replace(result, picks=(replace(result.picks[0], stock_code=identity),))
    with pytest.raises(dr.DailyRecommendationError, match="quarantine"):
        dr.write_outputs(forged, str(tmp_path / "leaked_pick"))
    assert not (tmp_path / "leaked_pick").exists()


@pytest.mark.parametrize("corruption", ["tradable", "reason", "other_security", "coherent_list_swap"])
def test_dynamic_audit_cannot_hide_exclusion_or_relabel_an_ordinary_security(
    run_security_recommend, tmp_path, corruption,
):
    result = run_security_recommend(scores={
        "SZ300001": 0.99, "SH600036": 0.98, "SH688766": 0.8,
    })
    frame = result.scored_frame.copy(deep=True)
    meta = copy.deepcopy(result.run_meta)
    if corruption == "tradable":
        frame.loc[frame["stock_code"].eq("SZ300001"), "tradable_flag"] = True
    elif corruption == "reason":
        frame.loc[frame["stock_code"].eq("SH600036"), "unavailable_reason"] = "suspended"
    elif corruption == "other_security":
        frame.loc[frame["stock_code"].eq("SH688766"), "unavailable_reason"] = "data_quarantine"
    else:
        # Even coherent outer/inner substitutions cannot legitimize the old
        # audit or an ordinary pick under the substituted security list.
        context = meta["suspension_quarantine"]
        context["instruments"] = ["SZ300001", "SH688766"]
        context["evidence"]["quarantined_ts_codes"] = ["300001.SZ", "688766.SH"]
    forged = replace(result, scored_frame=frame, run_meta=meta)
    with pytest.raises(dr.DailyRecommendationError, match="quarantine"):
        dr.write_outputs(forged, str(tmp_path / "bad_audit"))
    assert not (tmp_path / "bad_audit").exists()


@pytest.mark.parametrize("selected", [None, "suspend-688005-20260116-conflict"])
@pytest.mark.parametrize("broad", [False, True])
def test_dynamic_provider_still_requires_matching_authorization_before_model_work(
    run_security_recommend, tmp_path, monkeypatch, selected, broad,
):
    # The fixture configures only local synthetic collaborators. No inference
    # call is needed to create the actual integrity-stamp input for refusal.
    legacy._stamp(tmp_path / "bundle")
    model = Mock(side_effect=AssertionError("model must not load"))
    features = Mock(side_effect=AssertionError("features must not build"))
    monkeypatch.setattr(dr, "_load_model", model)
    monkeypatch.setattr(dr, "_build_asof_dataset", features)
    config = legacy._config(
        tmp_path, suspension_quarantine=selected, allow_holey_recommend=broad,
    )
    with pytest.raises(dr.DailyRecommendationError, match="quarantine"):
        dr.recommend(config, now=date(2026, 9, 22))
    model.assert_not_called()
    features.assert_not_called()


@pytest.mark.parametrize("broad", [False, True])
def test_dynamic_selector_never_waives_another_endpoint_hole(
    run_security_recommend, tmp_path, broad,
):
    extra = {"endpoint": "daily", "unit": "20260922", "reason_class": "timeout",
             "attempts": 3, "last_error": "other missing data"}
    legacy._stamp(tmp_path / "bundle", holes=[legacy._hole(), extra])
    with pytest.raises(dr.DailyRecommendationError, match="quarantine"):
        dr._assert_bundle_fetch_complete(
            str(tmp_path / "bundle"), allow_holey_recommend=broad,
            suspension_quarantine=_POLICY,
        )


@pytest.mark.parametrize("scored_affected", [False, True])
def test_actual_cli_parser_and_print_use_the_dynamic_evidence_list(
    run_security_recommend, tmp_path, monkeypatch, capsys, scored_affected,
):
    from scripts import daily_recommend as cli

    scores = {"SH688766": 0.8}
    if scored_affected:
        scores.update({"SZ300001": 0.99, "SH600036": 0.98})
    result = run_security_recommend(scores=scores)
    recommend = Mock(return_value=result)
    monkeypatch.setattr(cli, "setup_logging", lambda: None)
    monkeypatch.setattr(cli, "recommend", recommend)
    assert cli.main([
        "--model", "synthetic.pkl", "--fit-start", "2020-01-01",
        "--fit-end", "2025-01-01", "--suspension-quarantine", _POLICY,
        "--out-dir", str(tmp_path / "cli"),
    ]) == 0
    config = recommend.call_args.args[0]
    assert config.suspension_quarantine == _POLICY
    assert config.allow_holey_recommend is False
    printed = capsys.readouterr().out
    assert _POLICY in printed and "SZ300001;SH600036" in printed
    assert "(2 security)" in printed and "incomplete" in printed.lower()
    assert "existing holdings are NOT represented as sold" in printed
    assert f"n_quarantined={2 if scored_affected else 0}" in printed
