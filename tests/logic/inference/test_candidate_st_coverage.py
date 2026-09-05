"""Candidate-level current-ST evidence, exercised through recommend().

Only model/provider IO is stubbed. Snapshot parquet, date/schema guards,
score normalization, current-ST filtering and ranking execute normally.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from src.inference import daily_recommend as dr

_AS_OF = "2025-07-02"
_ENTRY = "2025-07-03"
_SCORES = {"SH600000": 0.9, "SH600001": 0.8, "SZ000001": 0.7}
_NAMES = [("600000.SH", "浦发银行"), ("600001.SH", "普通企业"), ("000001.SZ", "平安银行")]


@pytest.fixture
def run_recommend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import qlib.data

    monkeypatch.setattr(dr, "provider_uri_guard_message", lambda _uri: None)
    monkeypatch.setattr(dr, "init_qlib_canonical", lambda _config: None)
    monkeypatch.setattr(dr, "_assert_model_universe_match", lambda *_args: ("csi300", "a" * 64))
    monkeypatch.setattr(dr, "_assert_bundle_fetch_complete", lambda *_args, **_kw: None)
    monkeypatch.setattr(qlib.data, "D", SimpleNamespace(
        calendar=lambda: pd.date_range("2025-06-30", "2025-07-04"),
    ))
    features = pd.DataFrame(
        {"feature": [1.0]},
        index=pd.MultiIndex.from_tuples(
            [(pd.Timestamp(_AS_OF), "SH600000")], names=["datetime", "instrument"],
        ),
    )
    monkeypatch.setattr(dr, "_build_asof_dataset", lambda *_args: (object(), features))
    monkeypatch.setattr(dr, "_build_pit_provider", lambda _config: object())

    def run(
        records=None, *, scores=None, masked=(), suspended=(), one_price=(),
        cadence=1, frame=None,
    ):
        snapshot = tmp_path / "active_stocks.parquet"
        if frame is None:
            frame = pd.DataFrame(_NAMES if records is None else records, columns=["ts_code", "name"])
            frame["snapshot_date"] = "20250704"
        frame.to_parquet(snapshot)
        predictions = pd.Series(_SCORES if scores is None else scores, dtype=float)
        model = SimpleNamespace(predict=lambda *_args, **_kw: predictions)
        monkeypatch.setattr(dr, "_load_model", lambda _path: (model, "a" * 64))
        monkeypatch.setattr(dr, "compute_unavailable_mask", lambda *_args, **_kw: SimpleNamespace(masked=set(masked)))
        monkeypatch.setattr(dr, "_per_regime_sets", lambda *_args: (set(suspended), set(one_price)))
        config = dr.RecommendationConfig(
            model_path="synthetic.pkl", provider_uri="synthetic_bundle",
            delisted_registry_path="synthetic_registry.parquet",
            fit_start="2020-01-01", fit_end="2024-12-31", as_of_date=_AS_OF,
            name_source_parquet=str(snapshot), topk=1, rebalance_cadence_days=cadence,
        )
        return dr.recommend(config, now=date(2025, 7, 4))

    return run


@pytest.mark.parametrize("missing_index", [0, 1, 2])
def test_unmasked_missing_name_refuses_even_below_topk(run_recommend, missing_index):
    records = [row for i, row in enumerate(_NAMES) if i != missing_index]
    with pytest.raises(dr.DailyRecommendationError, match="current-ST.*missing") as exc:
        run_recommend(records)
    assert _NAMES[missing_index][0] in str(exc.value)
    assert "active_stocks.parquet" in str(exc.value)


@pytest.mark.parametrize("invalid", [None, float("nan"), pd.NA, "", " \t\n\u3000", 123, 1.5, True, False, b"ST"])
def test_original_invalid_names_refuse_instead_of_string_coercion(run_recommend, invalid):
    # Homogeneous raw columns survive a real Parquet round trip, including
    # numeric/bool names (not a mixed column that pyarrow rejects first).
    records = [(code, invalid) for code, _name in _NAMES]
    with pytest.raises(dr.DailyRecommendationError, match="current-ST.*invalid") as exc:
        run_recommend(records)
    assert "600000.SH" in str(exc.value)


@pytest.mark.parametrize("names", [
    ["*ST示例", "普通企业"], ["普通企业", "*ST示例"], ["普通企业", "普通企业"],
])
def test_duplicate_required_code_refuses_independent_of_name_order(run_recommend, names):
    records = [("600000.SH", name) for name in names] + _NAMES[1:]
    with pytest.raises(dr.DailyRecommendationError, match="current-ST.*duplicate"):
        run_recommend(records)


@pytest.mark.parametrize("reason", ["suspended", "one_price_lock", "unavailable"])
@pytest.mark.parametrize("bad_records", [[], [("600000.SH", None)], [
    ("600000.SH", "*ST示例"), ("600000.SH", "普通企业"),
]])
def test_authoritatively_masked_unknown_name_does_not_block(run_recommend, reason, bad_records):
    result = run_recommend(
        bad_records + _NAMES[1:], masked=[(_ENTRY, "SH600000")],
        suspended=["SH600000"] if reason == "suspended" else [],
        one_price=["SH600000"] if reason == "one_price_lock" else [],
    )
    assert [pick.stock_code for pick in result.picks] == ["SH600001"]
    audit = result.scored_frame.set_index("stock_code")
    assert audit.loc["SH600000", "unavailable_reason"] == reason
    assert not audit.loc["SH600000", "tradable_flag"]
    assert (result.n_scored, result.n_masked, result.n_st_excluded) == (2, 1, 0)


def test_reason_sets_alone_do_not_exempt_missing_candidate(run_recommend):
    with pytest.raises(dr.DailyRecommendationError, match="current-ST.*missing"):
        run_recommend(_NAMES[1:], suspended=["SH600000"], one_price=["SH600000"])


def test_mask_on_decision_day_does_not_exempt_entry_candidate(run_recommend):
    with pytest.raises(dr.DailyRecommendationError, match="current-ST.*missing"):
        run_recommend(_NAMES[1:], masked=[(_AS_OF, "SH600000")])


def test_nan_scored_and_unrelated_snapshot_rows_do_not_require_names(run_recommend):
    result = run_recommend(
        _NAMES[1:] + [("999999.SH", None), ("999999.SH", "*ST无关")],
        scores={**_SCORES, "SH600000": float("nan")},
    )
    assert [pick.stock_code for pick in result.picks] == ["SH600001"]
    assert len(result.scored_frame) == result.n_scored == 2


def test_complete_names_preserve_st_ranking_and_microstructure_precedence(run_recommend):
    result = run_recommend(
        [("600000.SH", "*ST示例"), ("600001.SH", "ST示例"), _NAMES[2]],
        masked=[(_ENTRY, "SH600000")], suspended=["SH600000"],
    )
    assert [pick.stock_code for pick in result.picks] == ["SZ000001"]
    assert [pick.rank for pick in result.picks] == [1]
    assert list(result.scored_frame["unavailable_reason"]) == ["suspended", "st", ""]
    assert (result.n_scored, result.n_masked, result.n_st_excluded) == (1, 1, 1)


def test_valid_names_preserve_hold_day_semantics(run_recommend):
    result = run_recommend(cadence=5)
    assert result.rebalance_day is False
    assert result.next_rebalance_date is None
    assert [pick.stock_code for pick in result.picks] == ["SH600000"]


def test_all_masked_pool_needs_no_candidate_names_but_still_needs_snapshot(run_recommend):
    masked = [(_ENTRY, inst) for inst in _SCORES]
    result = run_recommend([("999999.SH", "无关企业")], masked=masked)
    assert result.picks == ()
    assert (result.n_scored, result.n_masked, result.n_st_excluded) == (0, 3, 0)
    with pytest.raises(dr.DailyRecommendationError, match="zero rows"):
        run_recommend([], masked=masked)
    stale = pd.DataFrame(_NAMES, columns=["ts_code", "name"]).assign(snapshot_date="20250101")
    with pytest.raises(dr.DailyRecommendationError, match="stale"):
        run_recommend(frame=stale, masked=masked)


@pytest.mark.parametrize("dtype", ["string", "category"])
@pytest.mark.parametrize("missing", [False, True])
def test_nullable_name_columns_preserve_raw_value_checks(run_recommend, dtype, missing):
    frame = pd.DataFrame(_NAMES, columns=["ts_code", "name"]).assign(snapshot_date="20250704")
    frame["name"] = frame["name"].astype(dtype)
    if missing:
        frame.loc[0, "name"] = pd.NA
        with pytest.raises(dr.DailyRecommendationError, match="current-ST.*invalid"):
            run_recommend(frame=frame)
    else:
        assert run_recommend(frame=frame).picks[0].stock_name == "浦发银行"


def test_candidate_validation_reuses_single_snapshot_read(run_recommend, monkeypatch):
    reader = Mock(wraps=pd.read_parquet)
    monkeypatch.setattr(pd, "read_parquet", reader)
    run_recommend()
    assert reader.call_count == 1


@pytest.mark.parametrize("unrelated_code", [None, pd.NA, 123, ["600000.SH"], {"code": "600000.SH"}])
def test_unrelated_malformed_codes_do_not_break_candidate_validation(unrelated_code):
    frame = pd.DataFrame({
        "ts_code": ["600000.SH", unrelated_code], "name": ["浦发银行", pd.NA],
    })
    dr._validate_candidate_st_names(frame, {"600000.SH"}, source="synthetic.parquet")


def test_cli_candidate_failure_does_not_write_or_replace_artifacts(
    run_recommend, tmp_path, monkeypatch,
):
    from scripts import daily_recommend as cli

    output = tmp_path / "outputs"
    output.mkdir()
    existing = output / f"daily_recommendation_{_AS_OF}.json"
    existing.write_bytes(b"existing artifact must stay unchanged")
    writer = Mock(side_effect=AssertionError("failure must not reach writer"))
    monkeypatch.setattr(cli, "setup_logging", lambda: None)
    monkeypatch.setattr(cli, "recommend", lambda _config: run_recommend(_NAMES[1:]))
    monkeypatch.setattr(cli, "write_outputs", writer)
    assert cli.main([
        "--model", "synthetic.pkl", "--fit-start", "2020-01-01",
        "--fit-end", "2024-12-31", "--out-dir", str(output),
        "--instruments", "csi300", "--rebalance-cadence-days", "1",
    ]) == 1
    writer.assert_not_called()
    assert list(output.iterdir()) == [existing]
    assert existing.read_bytes() == b"existing artifact must stay unchanged"
