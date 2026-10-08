"""Synthetic serving/serialization regressions for the one approved incident."""

from __future__ import annotations

import csv
import json
from dataclasses import replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from src.inference import daily_recommend as dr

_POLICY = "suspend-688766-20251127-20251209"
_INSTRUMENT = "SH688766"
_AS_OF = "2026-09-21"
_ENTRY = "2026-09-22"
_SCORES = {_INSTRUMENT: 0.9, "SH600000": 0.8, "SZ000001": 0.7}
_CONTEXT_COLUMNS = [
    "suspension_quarantine_policy", "quarantined_instrument",
    "built_from_holey_fetch", "n_quarantined",
]
_COMBINED_POLICY = "suspend-688005-688766-observed-20261008"
_COMBINED_INSTRUMENTS = ["SH688005", "SH688766"]
_COMBINED_CONTEXT_COLUMNS = [
    "suspension_quarantine_policy", "quarantined_instruments",
    "built_from_holey_fetch", "n_quarantined",
]


def test_conflict_policy_excludes_only_688005_and_discloses_matching_evidence(
    run_recommend, monkeypatch, tmp_path,
):
    from web.operator_ui.pages._suspension_quarantine import quarantine_notice

    policy = "suspend-688005-20260116-conflict"
    evidence = {**_evidence(), "policy_id": policy, "missing_dates": ["20260116"]}
    monkeypatch.setattr(f"{__name__}._evidence", lambda: evidence)
    scores = {"SH688005": 0.99, "SH688766": 0.9, "SH600000": 0.8}
    result = run_recommend(scores=scores, policy=policy)
    assert [pick.stock_code for pick in result.picks] == ["SH688766"]
    audit = result.scored_frame.set_index("stock_code")
    assert audit["predicted_score"].to_dict() == scores
    assert audit.loc["SH688005", "unavailable_reason"] == "data_quarantine"
    assert audit.loc["SH688766", "tradable_flag"]
    assert result.run_meta["instruments"] == "csi300"
    assert result.run_meta["suspension_quarantine"]["instrument"] == "SH688005"
    projection = dr._quarantine_output_context(result)
    assert projection["quarantined_instrument"] == "SH688005"
    paths = dr.write_outputs(result, str(tmp_path / "out"))
    payload = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
    for key in ("csv", "audit"):
        with Path(paths[key]).open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                assert row["suspension_quarantine_policy"] == policy
                assert row["quarantined_instrument"] == "SH688005"
    notice = quarantine_notice(payload)
    assert "688005.SH" in notice and "冲突" in notice and "持仓" in notice
    payload["meta"]["suspension_quarantine"]["policy_id"] = _POLICY
    payload["meta"]["suspension_quarantine"]["instrument"] = _INSTRUMENT
    with pytest.raises(ValueError):
        quarantine_notice(payload)
    bad = replace(result, run_meta={**result.run_meta, "suspension_quarantine":
                                   payload["meta"]["suspension_quarantine"]})
    with pytest.raises(dr.DailyRecommendationError):
        dr.write_outputs(bad, str(tmp_path / "must-not-write"))
    assert not (tmp_path / "must-not-write").exists()


def _evidence():
    return {
        "policy_id": _POLICY,
        "missing_dates": ["20251127", "20251128", "20251201", "20251202",
                          "20251203", "20251204", "20251205", "20251209"],
        "reference_sha256": "a" * 64,
        "retained_sha256": "b" * 64,
        "candidate_sha256": "c" * 64,
        "query_start_date": "20100101",
        "query_end_date": "20260922",
    }


def _hole():
    return {
        "endpoint": "suspend_d", "unit": "file",
        "reason_class": "quarantined_history", "attempts": 1,
        "last_error": "known incident remains incomplete", "quarantine": _evidence(),
    }


def _stamp(bundle: Path, *, holes=None, clean=False):
    bundle.mkdir(exist_ok=True)
    payload = {
        "schema_version": 1 if clean else 2,
        "built_from_holey_fetch": not clean,
        "built_at": "2026-09-22T12:00:00+00:00",
        "holes": [] if clean else [_hole()] if holes is None else holes,
    }
    (bundle / "_fetch_integrity.json").write_text(json.dumps(payload), encoding="utf-8")
    return payload


def _config(tmp_path: Path, **updates):
    values = {
        "model_path": "synthetic.pkl", "provider_uri": str(tmp_path / "bundle"),
        "delisted_registry_path": "synthetic_registry.parquet",
        "fit_start": "2020-01-01", "fit_end": "2025-01-01",
        "instruments": "csi300", "as_of_date": _AS_OF, "topk": 1,
        "name_source_parquet": str(tmp_path / "names.parquet"),
    }
    values.update(updates)
    return dr.RecommendationConfig(**values)


def test_matching_policy_accepts_only_the_structured_incomplete_bundle(tmp_path):
    _stamp(tmp_path / "bundle")
    result = dr._assert_bundle_fetch_complete(
        str(tmp_path / "bundle"), allow_holey_recommend=False,
        suspension_quarantine=_POLICY,
    )
    assert result.built_from_holey_fetch is True
    assert result.holes[0].quarantine.to_dict() == _evidence()


@pytest.mark.parametrize("broad", [False, True])
def test_quarantine_needs_separate_opt_in_even_with_broad_override(tmp_path, broad):
    _stamp(tmp_path / "bundle")
    with pytest.raises(dr.DailyRecommendationError, match="quarantine"):
        dr._assert_bundle_fetch_complete(
            str(tmp_path / "bundle"), allow_holey_recommend=broad,
        )


@pytest.mark.parametrize("broad", [False, True])
def test_mixed_holes_cannot_be_accepted_as_the_single_incident(tmp_path, broad):
    extra = {"endpoint": "daily", "unit": "20260922", "reason_class": "timeout",
             "attempts": 3, "last_error": "other gap"}
    _stamp(tmp_path / "bundle", holes=[_hole(), extra])
    with pytest.raises(dr.DailyRecommendationError, match="quarantine"):
        dr._assert_bundle_fetch_complete(
            str(tmp_path / "bundle"), allow_holey_recommend=broad,
            suspension_quarantine=_POLICY,
        )


@pytest.mark.parametrize("policy", [None, _POLICY])
@pytest.mark.parametrize("broad", [False, True])
def test_policy_does_not_change_ordinary_hole_override_semantics(tmp_path, policy, broad):
    bundle = tmp_path / "bundle"
    hole = {"endpoint": "daily", "unit": "20260922", "reason_class": "timeout",
            "attempts": 3, "last_error": "ordinary gap"}
    payload = _stamp(bundle, holes=[hole])
    payload["schema_version"] = 1
    (bundle / "_fetch_integrity.json").write_text(json.dumps(payload), encoding="utf-8")
    if broad:
        integrity = dr._assert_bundle_fetch_complete(
            str(bundle), allow_holey_recommend=True, suspension_quarantine=policy,
        )
        assert integrity.built_from_holey_fetch is True
        assert integrity.holes[0].quarantine is None
    else:
        with pytest.raises(dr.DailyRecommendationError):
            dr._assert_bundle_fetch_complete(
                str(bundle), allow_holey_recommend=False, suspension_quarantine=policy,
            )


@pytest.mark.parametrize("corruption", ["missing", "hash", "date", "policy", "clean", "legacy"])
def test_corrupt_quarantine_cannot_be_bypassed(tmp_path, corruption):
    bundle = tmp_path / "bundle"
    payload = _stamp(bundle)
    evidence = payload["holes"][0]["quarantine"]
    if corruption == "missing":
        del payload["holes"][0]["quarantine"]
    elif corruption == "hash":
        evidence["candidate_sha256"] = "broken"
    elif corruption == "date":
        evidence["missing_dates"] = ["20251210"]
    elif corruption == "policy":
        evidence["policy_id"] = "other-policy"
    elif corruption == "clean":
        payload["built_from_holey_fetch"] = False
    else:
        payload["schema_version"] = 1
    (bundle / "_fetch_integrity.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(dr.DailyRecommendationError):
        dr._assert_bundle_fetch_complete(
            str(bundle), allow_holey_recommend=True, suspension_quarantine=_POLICY,
        )


@pytest.mark.parametrize("invalid", ["other-policy", "", True, 1, [], {}])
def test_unknown_policy_refuses_before_any_runtime(tmp_path, monkeypatch, invalid):
    runtime = Mock(side_effect=AssertionError("runtime must not start"))
    monkeypatch.setattr(dr, "init_qlib_canonical", runtime)
    config = _config(tmp_path, suspension_quarantine=invalid)
    with pytest.raises(dr.DailyRecommendationError, match="quarantine"):
        dr.recommend(config)
    runtime.assert_not_called()


@pytest.fixture
def run_recommend(tmp_path, monkeypatch):
    import qlib.data

    monkeypatch.setattr(dr, "provider_uri_guard_message", lambda _uri: None)
    monkeypatch.setattr(dr, "init_qlib_canonical", lambda _config: None)
    monkeypatch.setattr(dr, "_assert_model_universe_match", lambda *_args: ("csi300", "d" * 64))
    monkeypatch.setattr(qlib.data, "D", SimpleNamespace(
        calendar=lambda: pd.date_range("2026-09-18", "2026-09-22", freq="B"),
    ))
    monkeypatch.setattr(dr, "_build_pit_provider", lambda _config: object())

    def run(*, scores=None, clean=False, policy=_POLICY, overlap=False, topk=1,
            missing_quarantine_name=False):
        _stamp(tmp_path / "bundle", clean=clean)
        names = pd.DataFrame({
            "ts_code": ["688766.SH", "600000.SH", "000001.SZ", "688005.SH",
                        "600036.SH", "300001.SZ"],
            "name": ["*ST隔离" if overlap else "隔离样本", "浦发银行", "平安银行", "冲突样本",
                     "普通样本一", "普通样本二"],
            "snapshot_date": ["20260922"] * 6,
        })
        if missing_quarantine_name:
            names = names[names["ts_code"] != "688766.SH"]
        names.to_parquet(tmp_path / "names.parquet")
        predictions = pd.Series(_SCORES if scores is None else scores, dtype=float)
        features = pd.DataFrame(
            {"feature": range(len(predictions))},
            index=pd.MultiIndex.from_product(
                [[pd.Timestamp(_AS_OF)], list(predictions.index)],
                names=["datetime", "instrument"],
            ),
        )
        dataset = object()
        build = Mock(return_value=(dataset, features))
        predict = Mock(return_value=predictions)
        mask = Mock(return_value=SimpleNamespace(
            masked={(_ENTRY, _INSTRUMENT)} if overlap else set(),
        ))
        monkeypatch.setattr(dr, "_build_asof_dataset", build)
        monkeypatch.setattr(dr, "_load_model", lambda _path: (SimpleNamespace(predict=predict), "d" * 64))
        monkeypatch.setattr(dr, "compute_unavailable_mask", mask)
        monkeypatch.setattr(dr, "_per_regime_sets", lambda *_args: (
            {_INSTRUMENT} if overlap else set(), set(),
        ))
        config = _config(tmp_path, suspension_quarantine=policy, topk=topk)
        result = dr.recommend(config, now=date(2026, 9, 22))
        assert build.call_args.args == (config, _AS_OF)
        predict.assert_called_once_with(dataset, segment="infer")
        assert mask.call_args.args[0] == list(predictions.dropna().index)
        return result

    return run


@pytest.mark.parametrize("overlap", [False, True])
def test_quarantine_preserves_scoring_universe_and_refills_topk(run_recommend, overlap):
    result = run_recommend(overlap=overlap)
    assert [pick.stock_code for pick in result.picks] == ["SH600000"]
    audit = result.scored_frame.set_index("stock_code")
    assert list(audit.index) == list(_SCORES)
    assert audit["predicted_score"].to_dict() == _SCORES
    assert audit.loc[_INSTRUMENT, "unavailable_reason"] == "data_quarantine"
    assert not audit.loc[_INSTRUMENT, "tradable_flag"]
    assert (result.n_scored, result.n_masked, result.n_st_excluded, result.n_quarantined) == (2, 0, 0, 1)
    assert result.run_meta["instruments"] == "csi300"
    assert result.run_meta["suspension_quarantine"] == {
        "policy_id": _POLICY, "instrument": _INSTRUMENT,
        "built_from_holey_fetch": True, "evidence": _evidence(),
    }


@pytest.mark.parametrize("instrument", ["688766.SH", "sh688766"])
def test_existing_ticker_conversion_is_used_without_rewriting_scores(run_recommend, instrument):
    scores = {instrument: 0.9, "SH600000": 0.8}
    result = run_recommend(scores=scores)
    audit = result.scored_frame.set_index("stock_code")
    assert audit["predicted_score"].to_dict() == scores
    assert audit.loc[instrument, "unavailable_reason"] == "data_quarantine"
    assert result.n_quarantined == 1
    assert result.picks[0].stock_code == "SH600000"


@pytest.mark.parametrize("instrument", ["688766.sh", " SH688766", "SH688766 "])
def test_quarantine_does_not_add_unrecognised_ticker_aliases(run_recommend, instrument):
    with pytest.raises(dr.DailyRecommendationError):
        run_recommend(scores={instrument: 0.9, "SH600000": 0.8})


def test_quarantine_does_not_exempt_an_unmasked_name_from_st_source_coverage(run_recommend):
    with pytest.raises(dr.DailyRecommendationError, match="ST"):
        run_recommend(missing_quarantine_name=True)


@pytest.mark.parametrize("scored", [False, True])
def test_active_policy_is_disclosed_even_with_no_scored_quarantined_name(run_recommend, scored):
    scores = {"SH600000": 0.8}
    if scored:
        scores[_INSTRUMENT] = float("nan")
    result = run_recommend(scores=scores)
    assert result.n_quarantined == 0
    assert result.run_meta["suspension_quarantine"]["instrument"] == _INSTRUMENT
    assert result.run_meta["suspension_quarantine"]["built_from_holey_fetch"] is True


@pytest.mark.parametrize("policy", [None, _POLICY])
def test_complete_rebuild_clears_isolation_even_if_policy_remains_selected(run_recommend, policy):
    result = run_recommend(clean=True, policy=policy)
    assert result.picks[0].stock_code == _INSTRUMENT
    assert result.n_quarantined == 0
    assert "suspension_quarantine" not in result.run_meta


@pytest.mark.parametrize("topk", [0, 1])
@pytest.mark.parametrize("scored_quarantine", [False, True])
def test_json_and_both_csvs_disclose_active_quarantine_without_mutating_scores(
    run_recommend, tmp_path, topk, scored_quarantine,
):
    result = run_recommend(
        scores=_SCORES if scored_quarantine else {"SH600000": 0.8}, topk=topk,
    )
    original = result.scored_frame.copy(deep=True)
    paths = dr.write_outputs(result, str(tmp_path / "outputs"))
    payload = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
    assert payload["meta"]["suspension_quarantine"] == result.run_meta["suspension_quarantine"]
    assert payload["n_quarantined"] == int(scored_quarantine)
    assert payload["artifact_schema_version"] == 2
    assert all(row["stock_code"] != _INSTRUMENT for row in payload["picks"])
    for key in ("csv", "audit"):
        with Path(paths[key]).open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            assert reader.fieldnames[-4:] == _CONTEXT_COLUMNS
            rows = list(reader)
        assert len(rows) == (len(result.picks) if key == "csv" else len(original))
        for row in rows:
            assert row["suspension_quarantine_policy"] == _POLICY
            assert row["quarantined_instrument"] == _INSTRUMENT
            assert row["built_from_holey_fetch"] == "True"
            assert row["n_quarantined"] == str(int(scored_quarantine))
    pd.testing.assert_frame_equal(result.scored_frame, original)


def test_clean_output_omits_quarantine_context(run_recommend, tmp_path):
    result = run_recommend(clean=True)
    paths = dr.write_outputs(result, str(tmp_path / "outputs"))
    payload = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
    assert "suspension_quarantine" not in payload["meta"]
    assert "n_quarantined" not in payload
    for key in ("csv", "audit"):
        header = Path(paths[key]).read_text(encoding="utf-8-sig").splitlines()[0]
        assert not any(column in header for column in _CONTEXT_COLUMNS)


def test_empty_csvs_are_header_only_with_active_state_in_sibling_json(run_recommend, tmp_path):
    result = run_recommend(scores={"SH600000": 0.8})
    result = replace(result, picks=(), n_scored=0,
                     scored_frame=result.scored_frame.iloc[:0].copy())
    paths = dr.write_outputs(result, str(tmp_path / "outputs"))
    for key in ("csv", "audit"):
        with Path(paths[key]).open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            assert reader.fieldnames[-4:] == _CONTEXT_COLUMNS
            assert list(reader) == []
    payload = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
    assert payload["n_quarantined"] == 0
    assert payload["meta"]["suspension_quarantine"]["instrument"] == _INSTRUMENT
    assert payload["meta"]["suspension_quarantine"]["built_from_holey_fetch"] is True


@pytest.mark.parametrize("corruption", [
    "policy", "null_policy", "instrument", "null_instrument", "incomplete", "evidence",
    "count", "bool_count", "scored_count", "masked_count", "st_count",
    "leaked_pick", "leaked_audit", "missing_meta",
])
def test_writer_refuses_quarantine_inconsistency_before_any_output(
    run_recommend, tmp_path, corruption,
):
    result = run_recommend()
    meta = {**result.run_meta, "suspension_quarantine": dict(result.run_meta["suspension_quarantine"])}
    result = replace(result, run_meta=meta, scored_frame=result.scored_frame.copy())
    if corruption == "policy":
        meta["suspension_quarantine"]["policy_id"] = "other-policy"
    elif corruption == "null_policy":
        meta["suspension_quarantine"]["policy_id"] = pd.NA
    elif corruption == "instrument":
        meta["suspension_quarantine"]["instrument"] = "SH600000"
    elif corruption == "null_instrument":
        meta["suspension_quarantine"]["instrument"] = pd.NA
    elif corruption == "incomplete":
        meta["suspension_quarantine"]["built_from_holey_fetch"] = 1
    elif corruption == "evidence":
        meta["suspension_quarantine"]["evidence"] = None
    elif corruption == "count":
        result = replace(result, n_quarantined=0)
    elif corruption == "bool_count":
        result = replace(result, n_quarantined=True)
    elif corruption == "scored_count":
        result = replace(result, n_scored=3)
    elif corruption == "masked_count":
        result = replace(result, n_masked=1)
    elif corruption == "st_count":
        result = replace(result, n_st_excluded=1)
    elif corruption == "leaked_pick":
        result = replace(result, picks=(replace(result.picks[0], stock_code=_INSTRUMENT),))
    elif corruption == "leaked_audit":
        result.scored_frame.loc[0, "tradable_flag"] = True
    else:
        del meta["suspension_quarantine"]
    output = tmp_path / "not_created"
    with pytest.raises(dr.DailyRecommendationError, match="quarantine"):
        dr.write_outputs(result, str(output))
    assert not output.exists()


@pytest.mark.parametrize("instrument", ["SH688766", "sh688766", "688766.SH", " SH688766", pd.NA])
def test_writer_rejects_quarantined_or_malformed_pick_before_replacing_old_files(
    run_recommend, tmp_path, instrument,
):
    result = run_recommend()
    output = tmp_path / "outputs"
    paths = dr.write_outputs(result, str(output))
    old_bytes = {key: Path(path).read_bytes() for key, path in paths.items()}
    forged = replace(result, picks=(replace(result.picks[0], stock_code=instrument),))
    with pytest.raises(dr.DailyRecommendationError, match="quarantine"):
        dr.write_outputs(forged, str(output))
    assert {key: Path(path).read_bytes() for key, path in paths.items()} == old_bytes


def test_non_json_quarantine_mapping_refuses_before_replacing_existing_outputs(run_recommend, tmp_path):
    from types import MappingProxyType

    result = run_recommend()
    output = tmp_path / "outputs"
    paths = dr.write_outputs(result, str(output))
    before = {key: Path(path).read_bytes() for key, path in paths.items()}
    meta = dict(result.run_meta)
    meta["suspension_quarantine"] = MappingProxyType(dict(meta["suspension_quarantine"]))
    forged = replace(result, run_meta=meta)
    with pytest.raises(dr.DailyRecommendationError, match="quarantine"):
        dr.write_outputs(forged, str(output))
    assert {key: Path(path).read_bytes() for key, path in paths.items()} == before


def test_cli_forwards_only_the_explicit_policy_and_warns_on_zero_scored_count(
    run_recommend, tmp_path, monkeypatch, capsys,
):
    from scripts import daily_recommend as cli

    result = run_recommend(scores={"SH600000": 0.8})
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
    output = capsys.readouterr().out
    assert _POLICY in output and _INSTRUMENT in output
    assert "incomplete" in output.lower()
    assert "n_quarantined=0" in output


def test_cli_rejects_unknown_policy_without_running_recommend(monkeypatch):
    from scripts import daily_recommend as cli

    recommend = Mock(side_effect=AssertionError("must not recommend"))
    monkeypatch.setattr(cli, "setup_logging", lambda: None)
    monkeypatch.setattr(cli, "recommend", recommend)
    with pytest.raises(SystemExit) as exc:
        cli.main(["--suspension-quarantine", "other-policy"])
    assert exc.value.code == 2
    recommend.assert_not_called()


@pytest.fixture
def run_combined_recommend(run_recommend, monkeypatch):
    evidence = {**_evidence(), "policy_id": _COMBINED_POLICY}
    monkeypatch.setattr(f"{__name__}._evidence", lambda: evidence)

    def run(**updates):
        updates.setdefault("policy", _COMBINED_POLICY)
        return run_recommend(**updates)

    return run


def _combined_ui_payload(result):
    return {
        "meta": dict(result.run_meta), "n_quarantined": result.n_quarantined,
        "picks": [{"stock_code": pick.stock_code} for pick in result.picks],
    }


@pytest.mark.parametrize("identities", [
    ("SH688005", "SH688766"), ("688005.SH", "688766.SH"), ("sh688005", "sh688766"),
])
@pytest.mark.parametrize("overlap", [False, True])
def test_combined_isolation_excludes_both_before_topk_without_rewriting_score_audit(
    run_combined_recommend, tmp_path, identities, overlap,
):
    from web.operator_ui.pages._suspension_quarantine import quarantine_notice

    scores = {identities[0]: 0.99, identities[1]: 0.98, "SH600000": 0.8, "SZ000001": 0.7}
    result = run_combined_recommend(scores=scores, overlap=overlap, topk=2)
    assert [pick.stock_code for pick in result.picks] == ["SH600000", "SZ000001"]
    audit = result.scored_frame.set_index("stock_code")
    assert audit["predicted_score"].to_dict() == scores
    for identity in identities:
        assert audit.loc[identity, "unavailable_reason"] == "data_quarantine"
        assert not audit.loc[identity, "tradable_flag"]
    assert audit.loc["SH600000", "tradable_flag"]
    assert audit.loc["SZ000001", "tradable_flag"]
    assert (result.n_scored, result.n_masked, result.n_st_excluded, result.n_quarantined) == (2, 0, 0, 2)
    assert result.run_meta["instruments"] == "csi300"
    assert result.run_meta["suspension_quarantine"] == {
        "policy_id": _COMBINED_POLICY, "instruments": _COMBINED_INSTRUMENTS,
        "built_from_holey_fetch": True, "evidence": _evidence(),
    }
    assert dr._quarantine_output_context(result) == {
        "suspension_quarantine_policy": _COMBINED_POLICY,
        "quarantined_instruments": "SH688005;SH688766",
        "built_from_holey_fetch": True, "n_quarantined": 2,
    }
    paths = dr.write_outputs(result, str(tmp_path / "combined"))
    payload = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
    assert payload["n_quarantined"] == 2
    assert payload["meta"]["suspension_quarantine"] == result.run_meta["suspension_quarantine"]
    for key in ("csv", "audit"):
        with Path(paths[key]).open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        assert len(rows) == (2 if key == "csv" else 4)
        assert all(row["quarantined_instruments"] == "SH688005;SH688766" for row in rows)
        assert all(row["n_quarantined"] == "2" for row in rows)
    notice = quarantine_notice(payload)
    assert "688005.SH" in notice and "688766.SH" in notice
    assert "数据不完整" in notice and "持仓" in notice


@pytest.mark.parametrize("identity", _COMBINED_INSTRUMENTS)
def test_combined_scope_discloses_both_securities_when_only_one_has_a_score(
    run_combined_recommend, identity,
):
    result = run_combined_recommend(scores={identity: 0.99, "SH600000": 0.8})
    assert [pick.stock_code for pick in result.picks] == ["SH600000"]
    assert result.n_quarantined == 1
    assert result.run_meta["suspension_quarantine"]["instruments"] == _COMBINED_INSTRUMENTS
    assert dr._quarantine_output_context(result)["n_quarantined"] == 1


@pytest.mark.parametrize("nan_affected", [False, True])
def test_combined_disclosure_remains_active_with_zero_scored_exclusions(
    run_combined_recommend, nan_affected,
):
    from web.operator_ui.pages._suspension_quarantine import quarantine_notice

    scores = {"SH600000": 0.8}
    if nan_affected:
        scores.update({identity: float("nan") for identity in _COMBINED_INSTRUMENTS})
    result = run_combined_recommend(scores=scores)
    assert result.n_quarantined == 0
    assert result.run_meta["suspension_quarantine"]["instruments"] == _COMBINED_INSTRUMENTS
    assert "instrument" not in result.run_meta["suspension_quarantine"]
    assert result.run_meta["suspension_quarantine"]["built_from_holey_fetch"] is True
    projection = dr._quarantine_output_context(result)
    assert projection["quarantined_instruments"] == "SH688005;SH688766"
    assert projection["n_quarantined"] == 0
    notice = quarantine_notice(_combined_ui_payload(result))
    assert "688005.SH" in notice and "688766.SH" in notice


@pytest.mark.parametrize("row_count", [0, 1, 2, 4])
def test_combined_plural_csv_projection_is_scalar_for_empty_and_variable_row_exports(
    run_combined_recommend, tmp_path, row_count,
):
    eligible = {"SH600000": 0.8, "SZ000001": 0.7, "SH600036": 0.6, "SZ300001": 0.5}
    scores = dict(list(eligible.items())[:max(row_count, 1)])
    result = run_combined_recommend(scores=scores, topk=row_count)
    if row_count == 0:
        # A valid empty serialization boundary, not a fabricated runtime score.
        result = replace(result, picks=(), n_scored=0,
                         scored_frame=result.scored_frame.iloc[:0].copy())
    original = result.scored_frame.copy(deep=True)
    paths = dr.write_outputs(result, str(tmp_path / "combined_rows"))
    payload = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
    assert payload["meta"]["suspension_quarantine"]["instruments"] == _COMBINED_INSTRUMENTS
    assert payload["n_quarantined"] == 0
    assert len(payload["picks"]) == row_count
    for key in ("csv", "audit"):
        with Path(paths[key]).open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            assert reader.fieldnames[-4:] == _COMBINED_CONTEXT_COLUMNS
            assert "quarantined_instrument" not in reader.fieldnames
            rows = list(reader)
        assert len(rows) == row_count
        for row in rows:
            assert row["suspension_quarantine_policy"] == _COMBINED_POLICY
            assert row["quarantined_instruments"] == "SH688005;SH688766"
            assert row["built_from_holey_fetch"] == "True"
            assert row["n_quarantined"] == "0"
    pd.testing.assert_frame_equal(result.scored_frame, original)


@pytest.mark.parametrize("corruption", [
    "missing_field", "missing_security", "extra_security", "reordered", "string",
    "tuple", "null", "null_member", "bool", "pd_na", "singular", "both_shapes",
    "outer_legacy", "inner_legacy", "outer_conflict", "inner_conflict",
    "incomplete_false", "incomplete_int", "no_evidence", "no_meta",
])
def test_combined_export_and_readonly_ui_refuse_malformed_context_before_output(
    run_combined_recommend, tmp_path, corruption,
):
    from web.operator_ui.pages._suspension_quarantine import quarantine_notice

    result = run_combined_recommend(scores={
        "SH688005": 0.99, "SH688766": 0.98, "SH600000": 0.8,
    })
    context = dict(result.run_meta["suspension_quarantine"])
    context["evidence"] = dict(context["evidence"])
    meta = {**result.run_meta, "suspension_quarantine": context}
    if corruption == "missing_field":
        del context["instruments"]
    elif corruption == "missing_security":
        context["instruments"] = ["SH688005"]
    elif corruption == "extra_security":
        context["instruments"] = [*_COMBINED_INSTRUMENTS, "SH600000"]
    elif corruption == "reordered":
        context["instruments"] = list(reversed(_COMBINED_INSTRUMENTS))
    elif corruption == "string":
        context["instruments"] = "SH688005;SH688766"
    elif corruption == "tuple":
        context["instruments"] = tuple(_COMBINED_INSTRUMENTS)
    elif corruption == "null":
        context["instruments"] = None
    elif corruption == "null_member":
        context["instruments"] = ["SH688005", None]
    elif corruption == "bool":
        context["instruments"] = True
    elif corruption == "pd_na":
        context["instruments"] = pd.NA
    elif corruption == "singular":
        del context["instruments"]
        context["instrument"] = "SH688005"
    elif corruption == "both_shapes":
        context["instrument"] = "SH688005"
    elif corruption == "outer_legacy":
        context["policy_id"] = _POLICY
    elif corruption == "inner_legacy":
        context["evidence"]["policy_id"] = _POLICY
    elif corruption == "outer_conflict":
        context["policy_id"] = "suspend-688005-20260116-conflict"
    elif corruption == "inner_conflict":
        context["evidence"]["policy_id"] = "suspend-688005-20260116-conflict"
        context["evidence"]["missing_dates"] = ["20260116"]
    elif corruption == "incomplete_false":
        context["built_from_holey_fetch"] = False
    elif corruption == "incomplete_int":
        context["built_from_holey_fetch"] = 1
    elif corruption == "no_evidence":
        context["evidence"] = None
    else:
        del meta["suspension_quarantine"]
    forged = replace(result, run_meta=meta)
    output = tmp_path / "combined_must_not_create"
    with pytest.raises(dr.DailyRecommendationError, match="quarantine"):
        dr.write_outputs(forged, str(output))
    assert not output.exists()
    with pytest.raises(ValueError):
        quarantine_notice(_combined_ui_payload(forged))


@pytest.mark.parametrize("identity", [
    "SH688005", "SH688766", "688005.SH", "688766.SH", "sh688005", "sh688766",
])
def test_combined_writer_and_ui_reject_either_security_leaking_into_picks(
    run_combined_recommend, tmp_path, identity,
):
    from web.operator_ui.pages._suspension_quarantine import quarantine_notice

    result = run_combined_recommend(scores={
        "SH688005": 0.99, "SH688766": 0.98, "SH600000": 0.8,
    })
    output = tmp_path / "combined_existing"
    paths = dr.write_outputs(result, str(output))
    old_bytes = {key: Path(path).read_bytes() for key, path in paths.items()}
    forged = replace(result, picks=(replace(result.picks[0], stock_code=identity),))
    with pytest.raises(dr.DailyRecommendationError, match="quarantine"):
        dr.write_outputs(forged, str(output))
    assert {key: Path(path).read_bytes() for key, path in paths.items()} == old_bytes
    with pytest.raises(ValueError):
        quarantine_notice(_combined_ui_payload(forged))


@pytest.mark.parametrize("identity", _COMBINED_INSTRUMENTS)
def test_combined_writer_refuses_either_quarantined_audit_row_becoming_tradable(
    run_combined_recommend, tmp_path, identity,
):
    result = run_combined_recommend(scores={
        "SH688005": 0.99, "SH688766": 0.98, "SH600000": 0.8,
    })
    frame = result.scored_frame.copy(deep=True)
    frame.loc[frame["stock_code"].eq(identity), "tradable_flag"] = True
    forged = replace(result, scored_frame=frame)
    output = tmp_path / "combined_bad_audit"
    with pytest.raises(dr.DailyRecommendationError, match="quarantine"):
        dr.write_outputs(forged, str(output))
    assert not output.exists()


@pytest.mark.parametrize("selected", [None, _POLICY, "suspend-688005-20260116-conflict"])
@pytest.mark.parametrize("broad", [False, True])
def test_combined_bundle_refuses_absent_or_wrong_opt_in_before_model_work(
    run_combined_recommend, tmp_path, monkeypatch, selected, broad,
):
    _stamp(tmp_path / "bundle")
    model = Mock(side_effect=AssertionError("model must not load"))
    features = Mock(side_effect=AssertionError("features must not build"))
    monkeypatch.setattr(dr, "_load_model", model)
    monkeypatch.setattr(dr, "_build_asof_dataset", features)
    config = _config(tmp_path, suspension_quarantine=selected, allow_holey_recommend=broad)
    with pytest.raises(dr.DailyRecommendationError, match="quarantine"):
        dr.recommend(config, now=date(2026, 9, 22))
    model.assert_not_called()
    features.assert_not_called()


@pytest.mark.parametrize("broad", [False, True])
def test_combined_authorization_never_accepts_an_additional_generic_fetch_hole(
    run_combined_recommend, tmp_path, broad,
):
    extra = {"endpoint": "daily", "unit": "20260922", "reason_class": "timeout",
             "attempts": 3, "last_error": "unapproved missing data"}
    _stamp(tmp_path / "bundle", holes=[_hole(), extra])
    with pytest.raises(dr.DailyRecommendationError, match="quarantine"):
        dr._assert_bundle_fetch_complete(
            str(tmp_path / "bundle"), allow_holey_recommend=broad,
            suspension_quarantine=_COMBINED_POLICY,
        )


@pytest.mark.parametrize("policy,instrument", [
    (_POLICY, _INSTRUMENT), ("suspend-688005-20260116-conflict", "SH688005"),
])
def test_existing_single_security_policies_refuse_plural_context_substitution(
    run_recommend, tmp_path, monkeypatch, policy, instrument,
):
    from web.operator_ui.pages._suspension_quarantine import quarantine_notice

    evidence = {**_evidence(), "policy_id": policy}
    if policy != _POLICY:
        evidence["missing_dates"] = ["20260116"]
    monkeypatch.setattr(f"{__name__}._evidence", lambda: evidence)
    result = run_recommend(policy=policy)
    context = dict(result.run_meta["suspension_quarantine"])
    assert context["instrument"] == instrument
    assert "instruments" not in context
    del context["instrument"]
    context["instruments"] = [instrument]
    forged = replace(result, run_meta={**result.run_meta, "suspension_quarantine": context})
    output = tmp_path / "old_policy_plural_must_not_create"
    with pytest.raises(dr.DailyRecommendationError, match="quarantine"):
        dr.write_outputs(forged, str(output))
    assert not output.exists()
    with pytest.raises(ValueError):
        quarantine_notice(_combined_ui_payload(forged))


@pytest.mark.parametrize("scored_affected", [False, True])
def test_combined_cli_forwards_only_explicit_authority_and_discloses_both_securities(
    run_combined_recommend, tmp_path, monkeypatch, capsys, scored_affected,
):
    from scripts import daily_recommend as cli

    scores = {"SH600000": 0.8}
    if scored_affected:
        scores.update({"SH688005": 0.99, "SH688766": 0.98})
    result = run_combined_recommend(scores=scores)
    recommend = Mock(return_value=result)
    monkeypatch.setattr(cli, "setup_logging", lambda: None)
    monkeypatch.setattr(cli, "recommend", recommend)
    assert cli.main([
        "--model", "synthetic.pkl", "--fit-start", "2020-01-01",
        "--fit-end", "2025-01-01", "--suspension-quarantine", _COMBINED_POLICY,
        "--out-dir", str(tmp_path / "combined_cli"),
    ]) == 0
    config = recommend.call_args.args[0]
    assert config.suspension_quarantine == _COMBINED_POLICY
    assert config.allow_holey_recommend is False
    output = capsys.readouterr().out
    assert _COMBINED_POLICY in output
    assert all(identity in output for identity in _COMBINED_INSTRUMENTS)
    assert "incomplete" in output.lower()
    assert f"n_quarantined={2 if scored_affected else 0}" in output
