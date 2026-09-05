"""Real writer round trips: CSV cadence context must match JSON, not policy."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, replace
from pathlib import Path

import pandas as pd
import pytest

from src.inference.daily_recommend import (
    DailyRecommendationError,
    DailyRecommendationResult,
    RecommendationConfig,
    _assemble_run_meta,
    build_recommendation,
    write_outputs,
)

_BUY_COLUMNS = [
    "as_of_date", "entry_date", "rank", "stock_code", "stock_name",
    "predicted_score", "tradable_flag", "unavailable_reason",
]
_AUDIT_COLUMNS = [column for column in _BUY_COLUMNS if column != "rank"]
_CADENCE_COLUMNS = ["rebalance_day", "next_rebalance_date"]
_AS_OF = "2025-07-01"
_ENTRY = "2025-07-02"


def _result(marker, next_date, shape):
    scores = {} if shape == "empty" else {"SH600000": 0.9, "SZ000001": 0.8}
    picks, audit, excluded = build_recommendation(
        score_by_inst=scores, masked_pairs={"SZ000001"}, suspended={"SZ000001"},
        one_price=set(), name_fn=lambda _code: "示例企业", as_of_date=_AS_OF,
        entry_date=_ENTRY, topk=0 if shape == "no_picks" else 1,
    )
    config = RecommendationConfig(
        model_path="synthetic.pkl", provider_uri="synthetic_bundle",
        delisted_registry_path="registry.parquet", fit_start="2020-01-01",
        fit_end="2024-12-31",
    )
    return DailyRecommendationResult(
        as_of_date=_AS_OF, entry_date=_ENTRY, picks=picks,
        n_scored=len(scores) - excluded, n_masked=excluded, n_st_excluded=0,
        scored_frame=audit, run_meta=_assemble_run_meta(
            config, model_pkl_sha256="a" * 64, bundle_tag=None,
            generated_at="2025-07-02T18:00:00+08:00",
        ), rebalance_day=marker, next_rebalance_date=next_date,
    )


@pytest.mark.parametrize("marker,next_date", [
    (None, None), (True, _AS_OF), (False, "2025-07-07"), (False, None),
])
@pytest.mark.parametrize("shape", ["nonempty", "no_picks", "empty"])
def test_csvs_mirror_cadence_without_changing_daily_bytes_or_json(
    tmp_path, marker, next_date, shape,
):
    result = _result(marker, next_date, shape)
    original = result.scored_frame.copy(deep=True)
    buy_rows = [{"as_of_date": _AS_OF, "entry_date": _ENTRY, **asdict(p)} for p in result.picks]
    buy_frame = pd.DataFrame(buy_rows, columns=_BUY_COLUMNS)
    assert list(original.columns) == _AUDIT_COLUMNS  # actual producer shape, no rank
    paths = write_outputs(result, str(tmp_path))
    assert set(paths) == {"csv", "json", "audit"}
    for kind, frame in (("csv", buy_frame), ("audit", original)):
        base_columns = _BUY_COLUMNS if kind == "csv" else _AUDIT_COLUMNS
        read = pd.read_csv(paths[kind], dtype=str, keep_default_na=False)
        suffix = [] if marker is None else _CADENCE_COLUMNS
        assert list(read.columns) == base_columns + suffix
        assert len(read) == len(frame)
        assert Path(paths[kind]).read_bytes().startswith(b"\xef\xbb\xbf")
        if marker is None:
            assert Path(paths[kind]).read_bytes() == frame.to_csv(index=False).encode("utf-8-sig")
        else:
            assert list(read["rebalance_day"]) == [str(marker)] * len(frame)
            assert list(read["next_rebalance_date"]) == [next_date or ""] * len(frame)
            # Removing ONLY the two appended columns restores the old table.
            assert read[base_columns].to_csv(index=False) == frame.to_csv(index=False)

    expected_json = {
        "artifact_schema_version": 2, "as_of_date": _AS_OF, "entry_date": _ENTRY,
        "n_scored": result.n_scored, "n_masked": result.n_masked,
        "n_st_excluded": result.n_st_excluded, "picks": buy_rows,
        "meta": dict(result.run_meta),
    }
    if marker is not None:
        expected_json.update(rebalance_day=marker, next_rebalance_date=next_date)
    expected_bytes = json.dumps(expected_json, ensure_ascii=False, indent=2).replace("\n", os.linesep).encode("utf-8")
    assert Path(paths["json"]).read_bytes() == expected_bytes
    pd.testing.assert_frame_equal(result.scored_frame, original)


@pytest.mark.parametrize("marker", [0, 1, 0.0, 1.0, "False", "True", "", pd.NA])
@pytest.mark.parametrize("existing", [False, True])
def test_non_bool_cadence_marker_refuses_before_any_output_io(tmp_path, marker, existing):
    output = tmp_path / "artifacts"
    snapshots = {}
    if existing:
        output.mkdir()
        for suffix in (".csv", ".json", "_scored_full.csv"):
            path = output / f"daily_recommendation_{_AS_OF}{suffix}"
            path.write_bytes(b"previous generation")
            snapshots[path.name] = path.read_bytes()
    result = _result(marker, _AS_OF, "nonempty")
    with pytest.raises(DailyRecommendationError, match="rebalance_day.*bool"):
        write_outputs(result, str(output))
    if existing:
        assert {path.name: path.read_bytes() for path in output.iterdir()} == snapshots
    else:
        assert not output.exists()


@pytest.mark.parametrize("next_date", [None, "2025-07-07"])
def test_existing_rebalance_date_guard_still_runs_before_io(tmp_path, next_date):
    result = replace(_result(True, _AS_OF, "nonempty"), next_rebalance_date=next_date)
    output = tmp_path / "not-created"
    with pytest.raises(DailyRecommendationError, match="cadence invariant"):
        write_outputs(result, str(output))
    assert not output.exists()
