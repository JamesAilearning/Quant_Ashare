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
            "ts_code": ["688766.SH", "600000.SH", "000001.SZ", "688005.SH"],
            "name": ["*ST隔离" if overlap else "隔离样本", "浦发银行", "平安银行", "冲突样本"],
            "snapshot_date": ["20260922"] * 4,
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
