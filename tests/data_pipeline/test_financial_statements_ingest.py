"""Versioned financial-statement ingest (阶段8 Gate-2 PR-1).

Governance: both update_flag versions retained, a CHANGED re-fetch is appended
(never overwritten), an identical re-fetch is idempotent, content hashing is
deterministic + NA-stable, and a frame missing a PIT column fails loud.
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.data.tushare.financial_statements import (
    COL_FETCH_BATCH,
    FinancialIngestError,
    FinancialStatementIngestor,
    content_hash,
)


class _FakeClient:
    """Returns a queued frame (or None) per ``call`` (one call per ingest)."""

    def __init__(self, frames: list[pd.DataFrame | None]) -> None:
        self._frames = list(frames)

    def call(self, api_name: str, **params: object) -> pd.DataFrame | None:
        return self._frames.pop(0)


def _income_row(end_date: str, update_flag: str, revenue: float) -> dict[str, object]:
    row: dict[str, object] = {
        "ts_code": "000001.SZ", "end_date": end_date, "ann_date": "20220331",
        "f_ann_date": "20220331", "update_flag": update_flag, "revenue": revenue,
    }
    # every charter income DATA column must be present (a missing column fails
    # loud; per-row NA is legitimate missingness).
    for col in ("total_revenue", "oper_cost", "sell_exp", "admin_exp",
                "rd_exp", "int_exp", "fin_exp"):
        row[col] = pd.NA
    return row


def test_both_update_flag_versions_retained(tmp_path) -> None:
    frame = pd.DataFrame([
        _income_row("20211231", "0", 100.0),
        _income_row("20211231", "1", 100.0),
    ])
    ing = FinancialStatementIngestor(_FakeClient([frame]), tmp_path)
    res = ing.ingest("income", "000001.SZ", fetch_batch="b1")
    assert res.rows_new == 2
    assert res.rows_unchanged == 0  # first ingest: nothing pre-existed (codex #340 P3)
    stored = pd.read_parquet(tmp_path / "income" / "000001.SZ.parquet")
    assert set(stored["update_flag"].astype(str)) == {"0", "1"}  # neither dropped


def test_changed_refetch_appends_new_batch_never_overwrites(tmp_path) -> None:
    b1 = pd.DataFrame([_income_row("20211231", "0", 100.0)])
    b2 = pd.DataFrame([_income_row("20211231", "0", 200.0)])  # SAME key, changed value
    ing = FinancialStatementIngestor(_FakeClient([b1, b2]), tmp_path)
    ing.ingest("income", "000001.SZ", fetch_batch="b1")
    res2 = ing.ingest("income", "000001.SZ", fetch_batch="b2")

    assert res2.rows_new == 1 and res2.rows_changed == 1
    stored = pd.read_parquet(tmp_path / "income" / "000001.SZ.parquet")
    # BOTH versions physically present — the original 100.0 is NOT overwritten.
    assert len(stored) == 2
    assert set(stored["revenue"]) == {100.0, 200.0}
    assert set(stored[COL_FETCH_BATCH]) == {"b1", "b2"}


def test_revert_to_earlier_value_is_reappended(tmp_path) -> None:
    # 100 -> 200 -> 100: the third fetch reverts to an earlier value. It must be
    # re-appended (compared against the LATEST version, not any historical hash),
    # so latest-batch resolution exposes 100, not the stale 200 (codex #340 r5 P1).
    from src.data.pit.financial_pit_contract import resolve_current_versions
    frames = [pd.DataFrame([_income_row("20211231", "0", v)]) for v in (100.0, 200.0, 100.0)]
    ing = FinancialStatementIngestor(_FakeClient(frames), tmp_path)
    ing.ingest("income", "000001.SZ", fetch_batch="b1")
    ing.ingest("income", "000001.SZ", fetch_batch="b2")
    res3 = ing.ingest("income", "000001.SZ", fetch_batch="b3")
    assert res3.rows_new == 1 and res3.rows_changed == 1   # revert re-appended
    stored = pd.read_parquet(tmp_path / "income" / "000001.SZ.parquet")
    assert len(stored) == 3                                # all three retained
    current = resolve_current_versions(stored)
    assert len(current) == 1
    assert current.iloc[0]["revenue"] == 100.0             # reverted value, not 200
    assert current.iloc[0][COL_FETCH_BATCH] == "b3"


def test_identical_refetch_is_idempotent(tmp_path) -> None:
    frame = pd.DataFrame([_income_row("20211231", "0", 100.0)])
    ing = FinancialStatementIngestor(_FakeClient([frame, frame.copy()]), tmp_path)
    ing.ingest("income", "000001.SZ", fetch_batch="b1")
    res2 = ing.ingest("income", "000001.SZ", fetch_batch="b2")
    assert res2.rows_new == 0 and res2.rows_unchanged == 1
    stored = pd.read_parquet(tmp_path / "income" / "000001.SZ.parquet")
    assert len(stored) == 1  # no duplicate for identical content


def test_content_hash_deterministic_and_na_stable() -> None:
    a = pd.Series({"end_date": "20211231", "ann_date": "20220331",
                   "f_ann_date": "20220331", "update_flag": "0", "revenue": 100.0,
                   "oper_cost": pd.NA})
    b = a.copy()
    fields = ("revenue", "oper_cost")
    assert content_hash(a, fields) == content_hash(b, fields)
    c = a.copy()
    c["revenue"] = 101.0
    assert content_hash(a, fields) != content_hash(c, fields)
    # NA hashes the same regardless of None vs pd.NA vs float nan
    d = a.copy()
    d["oper_cost"] = None
    assert content_hash(a, fields) == content_hash(d, fields)


def test_missing_pit_column_fails_loud(tmp_path) -> None:
    bad = pd.DataFrame([{"ts_code": "000001.SZ", "ann_date": "20220331",
                         "f_ann_date": "20220331", "revenue": 100.0}])  # no end_date/update_flag
    ing = FinancialStatementIngestor(_FakeClient([bad]), tmp_path)
    with pytest.raises(FinancialIngestError, match="missing"):
        ing.ingest("income", "000001.SZ", fetch_batch="b1")


def test_missing_announcement_column_fails_loud(tmp_path) -> None:
    # provider drops the f_ann_date COLUMN (schema regression). A missing COLUMN
    # must fail loud — NOT be invented as NA, which would silently read every
    # row as "no announcement / unavailable" (codex #340 P1).
    bad = pd.DataFrame([{"ts_code": "000001.SZ", "end_date": "20211231",
                         "ann_date": "20220331", "update_flag": "0",
                         "revenue": 100.0}])  # no f_ann_date column
    ing = FinancialStatementIngestor(_FakeClient([bad]), tmp_path)
    with pytest.raises(FinancialIngestError, match="f_ann_date"):
        ing.ingest("income", "000001.SZ", fetch_batch="b1")


def test_missing_ts_code_column_fails_loud(tmp_path) -> None:
    # provider omits ts_code -> stored rows can't satisfy the logical key;
    # refuse before the append-only store is corrupted (codex #340 P2).
    bad = pd.DataFrame([{"end_date": "20211231", "ann_date": "20220331",
                         "f_ann_date": "20220331", "update_flag": "0",
                         "revenue": 100.0}])  # no ts_code column
    ing = FinancialStatementIngestor(_FakeClient([bad]), tmp_path)
    with pytest.raises(FinancialIngestError, match="ts_code"):
        ing.ingest("income", "000001.SZ", fetch_batch="b1")


def test_blank_logical_key_value_fails_loud(tmp_path) -> None:
    # column present but a row has NA in a logical-key field (end_date defines
    # the PIT report period) — must fail loud, not enter the store (codex #340 r6).
    row = _income_row("20211231", "0", 100.0)
    row["end_date"] = pd.NA
    ing = FinancialStatementIngestor(_FakeClient([pd.DataFrame([row])]), tmp_path)
    with pytest.raises(FinancialIngestError, match="end_date"):
        ing.ingest("income", "000001.SZ", fetch_batch="b1")
    # a blank update_flag is refused too
    row2 = _income_row("20211231", "0", 100.0)
    row2["update_flag"] = "  "
    ing2 = FinancialStatementIngestor(_FakeClient([pd.DataFrame([row2])]), tmp_path)
    with pytest.raises(FinancialIngestError, match="update_flag"):
        ing2.ingest("income", "000001.SZ", fetch_batch="b1")


def test_non_01_update_flag_fails_loud(tmp_path) -> None:
    # a non-0/1 update_flag ("2") is neither original nor revised; must fail loud
    # rather than form a stray current-version key (codex #340 r7 P2).
    row = _income_row("20211231", "2", 100.0)
    ing = FinancialStatementIngestor(_FakeClient([pd.DataFrame([row])]), tmp_path)
    with pytest.raises(FinancialIngestError, match="update_flag"):
        ing.ingest("income", "000001.SZ", fetch_batch="b1")


def test_float_coerced_update_flag_normalized(tmp_path) -> None:
    # "0.0" from a float column normalizes to "0" — not rejected, not a 3rd flag.
    row = _income_row("20211231", "0.0", 100.0)
    ing = FinancialStatementIngestor(_FakeClient([pd.DataFrame([row])]), tmp_path)
    ing.ingest("income", "000001.SZ", fetch_batch="b1")
    stored = pd.read_parquet(tmp_path / "income" / "000001.SZ.parquet")
    assert set(stored["update_flag"].astype(str)) == {"0"}


def test_ambiguous_duplicate_logical_key_fails_loud(tmp_path) -> None:
    # two rows with the SAME (ts_code, end_date, update_flag) but different
    # content (as a report_type/comp_type variant collision would produce) must
    # fail loud, not collapse arbitrarily / break idempotence (codex #340 r8 P2).
    r1 = _income_row("20211231", "0", 100.0)
    r2 = _income_row("20211231", "0", 200.0)  # same logical key, different revenue
    ing = FinancialStatementIngestor(_FakeClient([pd.DataFrame([r1, r2])]), tmp_path)
    with pytest.raises(FinancialIngestError, match="DIFFERENT statement contents"):
        ing.ingest("income", "000001.SZ", fetch_batch="b1")


def test_provider_none_fails_loud(tmp_path) -> None:
    # None = transport/quota failure, NOT an empty result — must not be recorded
    # as a successful empty fetch (codex #340 r3 P2).
    ing = FinancialStatementIngestor(_FakeClient([None]), tmp_path)
    with pytest.raises(FinancialIngestError, match="None"):
        ing.ingest("income", "000001.SZ", fetch_batch="b1")


def test_missing_data_column_fails_loud(tmp_path) -> None:
    # provider drops a charter DATA field (rd_exp) as a column (schema change) —
    # must fail loud, not invent it as all-NA (codex #340 r3 P2).
    row = _income_row("20211231", "0", 100.0)
    del row["rd_exp"]
    ing = FinancialStatementIngestor(_FakeClient([pd.DataFrame([row])]), tmp_path)
    with pytest.raises(FinancialIngestError, match="rd_exp"):
        ing.ingest("income", "000001.SZ", fetch_batch="b1")


def test_empty_frame_missing_columns_fails_loud(tmp_path) -> None:
    # an empty frame that LACKS required columns (bad-fields query / schema
    # regression) must fail loud, not pass as "no data" (codex #340 r9).
    bad_empty = pd.DataFrame(columns=["ts_code", "end_date"])  # missing most cols
    ing = FinancialStatementIngestor(_FakeClient([bad_empty]), tmp_path)
    with pytest.raises(FinancialIngestError, match="missing column"):
        ing.ingest("income", "000001.SZ", fetch_batch="b1")


def test_empty_frame_with_columns_is_legit_no_data(tmp_path) -> None:
    # empty but carries all required columns -> legitimate no-data (0 rows),
    # accepted (tushare returns the fields with 0 rows for a real empty result).
    good_empty = pd.DataFrame(columns=list(_income_row("20211231", "0", 0.0).keys()))
    ing = FinancialStatementIngestor(_FakeClient([good_empty]), tmp_path)
    res = ing.ingest("income", "000001.SZ", fetch_batch="b1")
    assert res.rows_fetched == 0 and res.rows_new == 0


def test_unknown_endpoint_rejected(tmp_path) -> None:
    ing = FinancialStatementIngestor(_FakeClient([]), tmp_path)
    with pytest.raises(FinancialIngestError, match="unknown financial endpoint"):
        ing.fetch("balance_sheet_typo", "000001.SZ")
