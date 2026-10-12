"""Synthetic regressions for the single next-session candidate path."""

from __future__ import annotations

import csv
import json
import re
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from src.inference import daily_recommend as dr
from tests.logic.inference import test_suspension_quarantine_recommend as legacy
from tests.logic.inference.candidate_test_helpers import (
    CN_TZ,
    read_calendar,
    write_calendar,
)

run_recommend = legacy.run_recommend


def test_real_alpha158_initialization_never_requests_forward_label_bars(monkeypatch):
    """Intercept the lowest loader call, not the returned inference frame.

    Alpha158 initializes every configured data group before DatasetH.prepare.
    DK_I/col_set=feature alone cannot prevent its default future-return label
    from reaching D.features. All actual handler/dataset code executes here;
    only the raw provider returns independent, tiny synthetic values.
    """
    from qlib.contrib.data.handler import Alpha158
    from qlib.data.dataset import loader

    decision = "2025-07-01"
    calls = []
    index = pd.MultiIndex.from_product(
        [["SH600000", "SZ000001"], pd.date_range("2025-06-30", decision)],
        names=["instrument", "datetime"],
    )

    def provider_features(instruments, fields, start_time, end_time, **kwargs):
        calls.append((tuple(instruments), tuple(fields), str(start_time), str(end_time)))
        assert pd.Timestamp(end_time) <= pd.Timestamp(decision), "future raw loader end"
        assert not any(re.search(r"Ref\([^,]+,\s*-\d", field) for field in fields), (
            "future label expression reached raw provider"
        )
        return pd.DataFrame(
            [[float(column + row + 1) for column in range(len(fields))]
             for row in range(len(index))], index=index, columns=fields,
        )

    monkeypatch.setattr(loader, "D", SimpleNamespace(
        instruments=lambda *_args, **_kwargs: ["SH600000", "SZ000001"],
        features=provider_features,
    ))
    config = dr.RecommendationConfig(
        model_path="synthetic.pkl", provider_uri="synthetic_bundle",
        delisted_registry_path="synthetic_registry.parquet", fit_start="2025-06-30",
        fit_end="2025-06-30", instruments="csi800",
    )
    dataset, frame = dr._build_asof_dataset(config, decision)
    assert type(dataset.handler) is Alpha158
    assert len(calls) == 2
    assert len(calls[0][1]) == 158
    assert calls[1][1] == ("$close",)  # unused current-day loader shape only
    assert frame.shape == (2, 158)
    assert frame.index.get_level_values("datetime").unique().tolist() == [pd.Timestamp(decision)]
    assert dataset.handler.learn_processors == []


def test_candidate_market_queries_never_read_after_decision_session(
    run_recommend, monkeypatch,
):
    """The actual generator must not consult entry bars, even if recorded.

    The shared fixture installs synthetic PIT/model collaborators immediately
    before calling recommend. Install the query traps at that same boundary,
    then delegate the original generator unchanged: this regression also runs
    against the pre-change implementation without needing new DTO imports.
    """
    original_recommend = dr.recommend
    queries = []

    def checked_recommend(config, **kwargs):
        original_mask = dr.compute_unavailable_mask
        original_regimes = dr._per_regime_sets

        def mask(instruments, start_date, end_date, *, pit_provider):
            queries.append(("mask", start_date, end_date))
            assert start_date == config.as_of_date, "future entry-day mask read"
            assert end_date == config.as_of_date, "future entry-day mask read"
            return original_mask(
                instruments, start_date, end_date, pit_provider=pit_provider,
            )

        def regimes(pit, instruments, as_of_date):
            queries.append(("regimes", as_of_date, as_of_date))
            assert as_of_date == config.as_of_date, "future entry-day reason read"
            return original_regimes(pit, instruments, as_of_date)

        monkeypatch.setattr(dr, "compute_unavailable_mask", mask)
        monkeypatch.setattr(dr, "_per_regime_sets", regimes)
        return original_recommend(config, **kwargs)

    monkeypatch.setattr(dr, "recommend", checked_recommend)
    result = run_recommend()
    assert len(queries) == 2
    assert result.as_of_date == legacy._AS_OF


def test_default_candidate_uses_latest_price_tail_without_any_entry_bar(run_recommend):
    result = run_recommend(as_of=None)
    assert result.as_of_date == "2026-09-22"
    assert result.entry_date == "2026-09-23"
    assert result.run_meta["latest_closed_session"] == "2026-09-22"
    assert result.scored_frame["as_of_date"].unique().tolist() == ["2026-09-22"]
    assert all(pick.tradable_flag is None for pick in result.picks)
    assert all(pick.eligible_as_of is True for pick in result.picks)


@pytest.mark.parametrize("clock,prices,expected", [
    (datetime(2026, 9, 22, 15, 29, 59, tzinfo=CN_TZ),
     ["2026-09-18", "2026-09-21"], ("2026-09-21", "2026-09-22")),
    (datetime(2026, 9, 22, 15, 30, tzinfo=CN_TZ),
     ["2026-09-18", "2026-09-21", "2026-09-22"], ("2026-09-22", "2026-09-23")),
    (datetime(2026, 9, 26, 12, tzinfo=CN_TZ),
     ["2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25"],
     ("2026-09-25", "2026-09-28")),
])
def test_candidate_close_cutoff_and_weekend_follow_arranged_sessions(
    tmp_path, clock, prices, expected,
):
    schedule = read_calendar(
        tmp_path / "schedule.json", now=clock,
        start_date="2026-09-18", end_date="2026-09-30",
    )
    assert dr.resolve_dates(None, prices, schedule=schedule, now=clock) == expected


def test_candidate_entry_uses_source_holiday_arrangement_not_weekday_arithmetic(tmp_path):
    clock = datetime(2026, 9, 30, 18, tzinfo=CN_TZ)
    schedule = read_calendar(
        tmp_path / "schedule.json", now=clock,
        start_date="2026-09-28", end_date="2026-10-12",
        closed_dates={"2026-10-01", "2026-10-02", "2026-10-05",
                      "2026-10-06", "2026-10-07"},
    )
    assert dr.resolve_dates(
        None, ["2026-09-28", "2026-09-29", "2026-09-30"],
        schedule=schedule, now=clock,
    ) == ("2026-09-30", "2026-10-08")


@pytest.mark.parametrize("prices", [
    ["2026-09-18", "2026-09-21"],  # one closed session missing
    ["2026-09-18", "2026-09-21", "2026-09-22", "2026-09-23"],  # future bar
])
def test_default_stale_or_future_price_tail_refuses_before_model_work(run_recommend, prices):
    with pytest.raises(dr.DailyRecommendationError):
        run_recommend(as_of=None, price_calendar=prices)
    for collaborator in run_recommend.calls.values():
        collaborator.assert_not_called()


@pytest.mark.parametrize("corruption", [
    "missing", "stale", "future_fetch", "naive_fetch", "unknown_source",
    "missing_today", "missing_next_open", "unknown_field",
])
def test_invalid_schedule_refuses_before_prediction_without_online_fallback(
    run_recommend, tmp_path, corruption,
):
    now = datetime(2026, 9, 22, 21, tzinfo=CN_TZ)
    path = tmp_path / "provided_calendar.json"
    if corruption != "missing":
        write_calendar(path, now=now, start_date="2026-09-18", end_date="2026-09-30")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if corruption == "stale":
            payload["fetched_at"] = "2026-09-14T20:00:00+08:00"
        elif corruption == "future_fetch":
            payload["fetched_at"] = "2026-09-22T21:00:01+08:00"
        elif corruption == "naive_fetch":
            payload["fetched_at"] = "2026-09-22T20:00:00"
        elif corruption == "unknown_source":
            payload["source_api"] = "weekday_guess"
        elif corruption == "missing_today":
            payload["rows"] = [row for row in payload["rows"] if row["cal_date"] != "20260922"]
        elif corruption == "missing_next_open":
            for row in payload["rows"]:
                if row["cal_date"] > "20260922":
                    row["is_open"] = 0
        else:
            payload["assume_next_weekday"] = True
        path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(dr.DailyRecommendationError):
        run_recommend(as_of=None, now=now, calendar_source=str(path))
    for collaborator in run_recommend.calls.values():
        collaborator.assert_not_called()


def test_explicit_past_candidate_does_not_reanchor_n5_to_next_session(run_recommend):
    rebalance = run_recommend(as_of="2026-09-21", cadence=5)
    hold = run_recommend(as_of="2026-09-22", cadence=5)
    assert rebalance.run_meta["latest_closed_session"] == hold.run_meta["latest_closed_session"]
    assert rebalance.run_meta["latest_closed_session"] == "2026-09-22"
    assert rebalance.rebalance_day is True
    assert rebalance.next_rebalance_date == "2026-09-21"
    assert hold.rebalance_day is False
    assert hold.next_rebalance_date is None  # never invent a recorded cadence session
    assert rebalance.entry_date == "2026-09-22"
    assert hold.entry_date == "2026-09-23"


def test_all_candidate_outputs_disclose_unknown_entry_conditions_even_with_known_mask(
    run_recommend, tmp_path,
):
    result = run_recommend(clean=True, overlap=True)
    paths = dr.write_outputs(result, str(tmp_path / "out"))
    payload = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
    assert payload["artifact_schema_version"] == 3
    assert payload["recommendation_mode"] == "next_session_candidate"
    assert payload["entry_tradability_status"] == "unconfirmed"
    assert payload["n_quarantined"] == 0
    assert all(row["tradable_flag"] is None for row in payload["picks"])
    audit = result.scored_frame.set_index("stock_code")
    assert not audit.loc[legacy._INSTRUMENT, "eligible_as_of"]
    assert audit.loc[legacy._INSTRUMENT, "tradable_flag"] is None
    for kind in ("csv", "audit"):
        with Path(paths[kind]).open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        assert rows
        assert all(row["recommendation_mode"] == "next_session_candidate" for row in rows)
        assert all(row["entry_tradability_status"] == "unconfirmed" for row in rows)
        assert all(row["tradable_flag"] == "" for row in rows)


@pytest.mark.parametrize("corruption", [
    "audit_true", "audit_false", "audit_zero", "audit_nan", "pick_true",
    "pick_eligible_int", "pick_ineligible", "count", "score_inf",
    "missing_calendar", "missing_latest", "naive_generated", "stale_calendar",
])
@pytest.mark.parametrize("existing", [False, True])
def test_candidate_writer_refuses_incoherent_semantics_before_touching_outputs(
    run_recommend, tmp_path, corruption, existing,
):
    result = run_recommend(clean=True)
    if corruption.startswith("audit_"):
        audit = result.scored_frame.copy(deep=True)
        audit.loc[audit.index[0], "tradable_flag"] = {
            "audit_true": True, "audit_false": False,
            "audit_zero": 0, "audit_nan": float("nan"),
        }[corruption]
        result = replace(result, scored_frame=audit)
    elif corruption in {"pick_true", "pick_eligible_int", "pick_ineligible", "score_inf"}:
        updates = {
            "pick_true": {"tradable_flag": True},
            "pick_eligible_int": {"eligible_as_of": 1},
            "pick_ineligible": {"eligible_as_of": False},
            "score_inf": {"predicted_score": float("inf")},
        }[corruption]
        result = replace(result, picks=[replace(result.picks[0], **updates)])
    elif corruption == "count":
        result = replace(result, n_scored=result.n_scored + 1)
    else:
        meta = dict(result.run_meta)
        if corruption == "missing_calendar":
            del meta["trading_calendar"]
        elif corruption == "missing_latest":
            del meta["latest_closed_session"]
        elif corruption == "naive_generated":
            meta["generated_at"] = "2026-09-22T21:00:00"
        else:
            meta["trading_calendar"] = {**meta["trading_calendar"],
                                        "fetched_at": "2026-09-14T21:00:00+08:00"}
        result = replace(result, run_meta=meta)
    output = tmp_path / "candidate_outputs"
    previous = {}
    if existing:
        output.mkdir()
        for name in ("daily_recommendation.csv", "daily_recommendation.json", "audit.csv"):
            (output / name).write_bytes(b"previous accepted generation")
        previous = {path.name: path.read_bytes() for path in output.iterdir()}
    with pytest.raises(dr.DailyRecommendationError):
        dr.write_outputs(result, str(output))
    if existing:
        assert {path.name: path.read_bytes() for path in output.iterdir()} == previous
    else:
        assert not output.exists()


def _assert_writer_refuses_before_output_io(result, forged, output, *, existing):
    """Compare the real accepted three-file generation, not unrelated sentinels."""
    previous = {}
    if existing:
        paths = dr.write_outputs(result, str(output))
        assert set(paths) == {"csv", "json", "audit"}
        previous = {path.name: path.read_bytes() for path in output.iterdir()}
        assert len(previous) == 3
    with pytest.raises(dr.DailyRecommendationError):
        dr.write_outputs(forged, str(output))
    if existing:
        assert {path.name: path.read_bytes() for path in output.iterdir()} == previous
    else:
        assert not output.exists()


@pytest.mark.parametrize("rebalance_day,next_date", [
    (False, "tomorrow"),
    (False, "2026-09-21"),  # before the actual 09-22 planned entry
    (False, "2026-09-26"),  # Saturday, not a recorded N5 session
    (None, "2026-09-28"),  # daily results cannot silently discard a next date
])
@pytest.mark.parametrize("existing", [False, True])
def test_candidate_writer_refuses_invalid_cadence_before_output_io(
    run_recommend, tmp_path, rebalance_day, next_date, existing,
):
    result = run_recommend(clean=True)
    assert (result.as_of_date, result.entry_date) == ("2026-09-21", "2026-09-22")
    forged = replace(result, rebalance_day=rebalance_day, next_rebalance_date=next_date)
    _assert_writer_refuses_before_output_io(
        result, forged, tmp_path / "cadence_outputs", existing=existing,
    )


# Required shared keys written by daily_recommend._assemble_run_meta. Identity
# is tested separately because single-model SHA and ensemble are an XOR, while
# model_universe is genuinely optional and must not become required here.
_REQUIRED_SHARED_META = (
    "generated_at", "model_path", "fit_start_for_inference", "fit_end_for_inference",
    "provider_uri", "bundle_tag", "bundle_built_at", "instruments", "topk",
    "trading_calendar", "latest_closed_session",
)


@pytest.mark.parametrize("field", _REQUIRED_SHARED_META)
@pytest.mark.parametrize("corruption", ["missing", "wrong_type"])
@pytest.mark.parametrize("existing", [False, True])
def test_candidate_writer_requires_complete_typed_generation_meta_before_output_io(
    run_recommend, tmp_path, field, corruption, existing,
):
    result = run_recommend(clean=True)
    meta = dict(result.run_meta)
    assert field in meta  # real producer fixture, not a guessed reader field
    if corruption == "missing":
        del meta[field]
    else:
        meta[field] = True if field == "topk" else []
    _assert_writer_refuses_before_output_io(
        result, replace(result, run_meta=meta), tmp_path / "meta_outputs", existing=existing,
    )


def _producer_ensemble_result(result):
    """Use the real meta writer with the exact ensemble projection it accepts."""
    ensemble = {
        "schema_version": "csi800_n5_ensemble_manifest_v1",
        "manifest_path": "synthetic-ensemble.json",
        "manifest_sha256": "a" * 64,
        "blend": "mean_skipna",
        "n_models": 3,
        "members": [
            {"pkl_path": "older.pkl", "pkl_sha256": "b" * 64,
             "fit_start": "2023-10-01", "fit_end": "2025-09-30"},
            {"pkl_path": "middle.pkl", "pkl_sha256": "c" * 64,
             "fit_start": "2024-01-01", "fit_end": "2025-12-31"},
            {"pkl_path": "newest.pkl", "pkl_sha256": "d" * 64,
             "fit_start": "2024-04-01", "fit_end": "2026-03-31"},
        ],
    }
    config = dr.RecommendationConfig(
        model_path="ignored-single.pkl", provider_uri=result.run_meta["provider_uri"],
        delisted_registry_path="synthetic_registry.parquet", instruments="csi800",
        fit_start="2024-04-01", fit_end="2026-03-31", topk=result.run_meta["topk"],
        ensemble_manifest_path=ensemble["manifest_path"],
    )
    meta = dr._assemble_run_meta(
        config, model_pkl_sha256=None, ensemble=ensemble,
        bundle_tag=result.run_meta["bundle_tag"],
        bundle_built_at=result.run_meta["bundle_built_at"],
        trading_calendar=result.run_meta["trading_calendar"],
        latest_closed_session=result.run_meta["latest_closed_session"],
        generated_at=result.run_meta["generated_at"],
    )
    return replace(result, run_meta=meta)


def test_candidate_writer_preserves_the_real_ensemble_meta_projection(run_recommend, tmp_path):
    result = _producer_ensemble_result(run_recommend(clean=True))
    paths = dr.write_outputs(result, str(tmp_path / "ensemble_outputs"))
    payload = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
    assert payload["meta"] == result.run_meta
    assert "model_pkl_sha256" not in payload["meta"]
    assert payload["meta"]["model_path"] == payload["meta"]["ensemble"]["manifest_path"]


@pytest.mark.parametrize("corruption", [
    "single_missing", "single_null", "single_wrong_type", "both_identities",
    "ensemble_wrong_type", "ensemble_empty", "ensemble_missing_manifest_sha",
    "ensemble_wrong_count", "ensemble_missing_member_sha", "ensemble_wrong_members_type",
    "ensemble_consistent_count_1", "ensemble_consistent_count_2", "ensemble_consistent_count_4",
    "ensemble_duplicate_member_path", "ensemble_duplicate_member_sha",
])
@pytest.mark.parametrize("existing", [False, True])
def test_candidate_writer_refuses_missing_or_corrupt_model_identity_before_output_io(
    run_recommend, tmp_path, corruption, existing,
):
    result = run_recommend(clean=True)
    if corruption.startswith("single_"):
        meta = dict(result.run_meta)
        if corruption == "single_missing":
            del meta["model_pkl_sha256"]
        else:
            meta["model_pkl_sha256"] = None if corruption == "single_null" else []
    else:
        result = _producer_ensemble_result(result)
        meta = dict(result.run_meta)
        ensemble = dict(meta["ensemble"])
        if corruption == "both_identities":
            meta["model_pkl_sha256"] = "e" * 64
        elif corruption == "ensemble_wrong_type":
            meta["ensemble"] = []
        elif corruption == "ensemble_empty":
            meta["ensemble"] = {}
        elif corruption == "ensemble_missing_manifest_sha":
            del ensemble["manifest_sha256"]
        elif corruption == "ensemble_wrong_count":
            ensemble["n_models"] = 2
        elif corruption.startswith("ensemble_consistent_count_"):
            count = int(corruption.rsplit("_", 1)[1])
            members = [dict(member) for member in ensemble["members"]]
            if count == 4:
                members.append({
                    "pkl_path": "fourth.pkl", "pkl_sha256": "e" * 64,
                    "fit_start": "2024-07-01", "fit_end": "2026-06-30",
                })
            ensemble["members"] = members[:count]
            ensemble["n_models"] = count
            assert len(ensemble["members"]) == ensemble["n_models"]
            assert len({member["pkl_path"] for member in ensemble["members"]}) == count
            assert len({member["pkl_sha256"] for member in ensemble["members"]}) == count
        elif corruption in {"ensemble_duplicate_member_path", "ensemble_duplicate_member_sha"}:
            members = [dict(member) for member in ensemble["members"]]
            field = "pkl_path" if corruption == "ensemble_duplicate_member_path" else "pkl_sha256"
            members[1][field] = members[0][field]
            ensemble["members"] = members
            unchanged = "pkl_sha256" if field == "pkl_path" else "pkl_path"
            assert ensemble["n_models"] == len(members) == 3
            assert len({member[unchanged] for member in members}) == 3
        elif corruption == "ensemble_missing_member_sha":
            members = [dict(member) for member in ensemble["members"]]
            del members[0]["pkl_sha256"]
            ensemble["members"] = members
        else:
            ensemble["members"] = {}
        if corruption not in {"ensemble_wrong_type", "ensemble_empty"}:
            meta["ensemble"] = ensemble
    _assert_writer_refuses_before_output_io(
        result, replace(result, run_meta=meta), tmp_path / "identity_outputs", existing=existing,
    )


def test_top50_candidate_selection_does_not_turn_unknown_conditions_into_tradable():
    scores = {f"SH{600000 + index:06d}": 100.0 - index for index in range(60)}
    picks, audit, excluded = dr.build_recommendation(
        score_by_inst=scores, masked_pairs=set(), suspended=set(), one_price=set(),
        name_fn=lambda _instrument: "synthetic issuer", as_of_date="2026-09-22",
        entry_date="2026-09-23", topk=50,
    )
    assert excluded == 0 and len(picks) == 50 and len(audit) == 60
    assert [pick.rank for pick in picks] == list(range(1, 51))
    assert [pick.stock_code for pick in picks] == list(scores)[:50]
    assert all(pick.eligible_as_of is True and pick.tradable_flag is None for pick in picks)
    assert audit["tradable_flag"].map(lambda value: value is None).all()
