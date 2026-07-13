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
    # two rows with the SAME full versioned identity (ts_code, end_date,
    # update_flag, f_ann_date) but different content (a report_type/comp_type
    # variant collision) must fail loud, not collapse arbitrarily / break
    # idempotence (codex #340 r8 P2).
    r1 = _income_row("20211231", "0", 100.0)
    r2 = _income_row("20211231", "0", 200.0)  # same identity, different revenue
    ing = FinancialStatementIngestor(_FakeClient([pd.DataFrame([r1, r2])]), tmp_path)
    with pytest.raises(FinancialIngestError, match="DIFFERENT statement contents"):
        ing.ingest("income", "000001.SZ", fetch_batch="b1")


def test_same_triple_different_f_ann_date_both_stored(tmp_path) -> None:
    # the provider emits, for a few (ts_code, end_date, update_flag) triples,
    # TWO disclosures distinguishable only by f_ann_date (e.g. 五粮液 income
    # 20250630/uf1). Each is a distinct dated disclosure event — BOTH stored,
    # no hole (spec fix-financial-ingest-ambiguous-duplicates; this exact
    # pattern holed 27 instrument/endpoints in the Step-A full ingest).
    r1 = _income_row("20250630", "1", 527.7)
    r2 = _income_row("20250630", "1", 235.1)
    r2["f_ann_date"] = "20260430"  # late re-announcement, own date
    ing = FinancialStatementIngestor(_FakeClient([pd.DataFrame([r1, r2])]), tmp_path)
    res = ing.ingest("income", "000001.SZ", fetch_batch="b1")
    assert res.rows_new == 2
    stored = pd.read_parquet(tmp_path / "income" / "000001.SZ.parquet")
    assert len(stored) == 2
    assert set(stored["revenue"]) == {527.7, 235.1}


def test_same_f_ann_different_ann_double_content_refused(tmp_path) -> None:
    # same f_ann_date (the EFFECTIVE announcement day) with different ann_date
    # and different content: the announcement day cannot order them into a
    # record — must be refused as ambiguous, not slip through the raw-column
    # key (codex #351 r2).
    r1 = _income_row("20211231", "0", 100.0)
    r2 = _income_row("20211231", "0", 200.0)
    r2["ann_date"] = "20220430"  # f_ann_date identical on both
    ing = FinancialStatementIngestor(_FakeClient([pd.DataFrame([r1, r2])]), tmp_path)
    with pytest.raises(FinancialIngestError, match="DIFFERENT statement contents"):
        ing.ingest("income", "000001.SZ", fetch_batch="b1")


def test_fallback_dated_pair_distinct_ann_dates_both_stored(tmp_path) -> None:
    # blank f_ann_date on BOTH rows but DISTINCT ann_date (the fallback dating
    # path): still two distinct dated disclosures — ann_date is in the identity
    # too, so they must not share one NA key and be refused/collapsed
    # (codex #351).
    r1 = _income_row("20211231", "0", 100.0)
    r2 = _income_row("20211231", "0", 150.0)
    r1["f_ann_date"] = None
    r2["f_ann_date"] = None
    r2["ann_date"] = "20220430"  # r1 keeps 20220331
    ing = FinancialStatementIngestor(_FakeClient([pd.DataFrame([r1, r2])]), tmp_path)
    res = ing.ingest("income", "000001.SZ", fetch_batch="b1")
    assert res.rows_new == 2
    stored = pd.read_parquet(tmp_path / "income" / "000001.SZ.parquet")
    assert len(stored) == 2


def test_float_coerced_announcement_date_is_same_disclosure(tmp_path) -> None:
    # a re-fetch spelling the SAME announcement as a float-coerced '20220331.0'
    # (dtype drift) must be the SAME logical key + content — idempotent, not a
    # duplicated disclosure (codex #351 r3).
    b1 = pd.DataFrame([_income_row("20211231", "0", 100.0)])       # "20220331"
    r2 = _income_row("20211231", "0", 100.0)
    r2["f_ann_date"] = "20220331.0"
    r2["ann_date"] = "20220331.0"
    ing = FinancialStatementIngestor(_FakeClient([b1, pd.DataFrame([r2])]), tmp_path)
    ing.ingest("income", "000001.SZ", fetch_batch="b1")
    res2 = ing.ingest("income", "000001.SZ", fetch_batch="b2")
    assert res2.rows_new == 0 and res2.rows_unchanged == 1
    stored = pd.read_parquet(tmp_path / "income" / "000001.SZ.parquet")
    assert len(stored) == 1                       # no phantom second disclosure


def test_ann_only_correction_is_same_disclosure_not_new_identity(tmp_path) -> None:
    # provider corrects ONLY ann_date while f_ann_date (the effective
    # announcement) is present and unchanged: same disclosure identity — a
    # changed re-fetch (latest batch wins), NEVER a phantom second disclosure
    # that would tie on the served announcement day (codex #351 r5).
    from datetime import date as _date

    from src.data.pit.financial_pit_contract import (
        build_contract_frame,
        resolve_current_versions,
    )
    from src.data.trading_calendar import StaticTradingCalendar
    b1 = pd.DataFrame([_income_row("20211231", "0", 100.0)])   # ann 20220331
    r2 = _income_row("20211231", "0", 100.0)
    r2["ann_date"] = "20220330"                                # ann-only fix
    ing = FinancialStatementIngestor(_FakeClient([b1, pd.DataFrame([r2])]), tmp_path)
    ing.ingest("income", "000001.SZ", fetch_batch="b1")
    res2 = ing.ingest("income", "000001.SZ", fetch_batch="b2")
    assert res2.rows_new == 1 and res2.rows_changed == 1       # same identity
    stored = pd.read_parquet(tmp_path / "income" / "000001.SZ.parquet")
    cal = StaticTradingCalendar([_date(2022, 3, 31), _date(2022, 4, 1)])
    current = resolve_current_versions(build_contract_frame(stored, cal))
    assert len(current) == 1                                   # ONE disclosure
    assert current.iloc[0]["ann_date"] == "20220330"           # correction wins


def test_legacy_store_spelling_does_not_mint_phantom_disclosure(tmp_path) -> None:
    # a store written BEFORE canonicalization may spell the announcement as a
    # float-coerced '20220331.0'. A canonical re-fetch of the SAME disclosure
    # must key-match it in memory (no phantom second disclosure), and read-time
    # resolution must collapse to ONE current row (codex #351 r4).
    from datetime import date as _date

    from src.data.pit.financial_pit_contract import (
        build_contract_frame,
        resolve_current_versions,
    )
    from src.data.trading_calendar import StaticTradingCalendar
    legacy = _income_row("20211231", "0", 100.0)
    legacy["f_ann_date"] = "20220331.0"
    legacy["ann_date"] = "20220331.0"
    legacy_df = pd.DataFrame([legacy])
    legacy_df["_content_hash"] = "legacy_hash_over_old_spelling"
    legacy_df["_source_endpoint"] = "income"
    legacy_df["_fetch_batch"] = "b0"
    (tmp_path / "income").mkdir(parents=True)
    legacy_df.to_parquet(tmp_path / "income" / "000001.SZ.parquet", index=False)

    refetch = pd.DataFrame([_income_row("20211231", "0", 100.0)])  # canonical
    ing = FinancialStatementIngestor(_FakeClient([refetch]), tmp_path)
    res = ing.ingest("income", "000001.SZ", fetch_batch="b1")
    # hash may differ (legacy spelling hashed differently) -> appended as a
    # CHANGED re-fetch of the SAME identity, never a new disclosure key.
    assert res.rows_new <= 1
    stored = pd.read_parquet(tmp_path / "income" / "000001.SZ.parquet")
    cal = StaticTradingCalendar([_date(2022, 3, 31), _date(2022, 4, 1)])
    current = resolve_current_versions(build_contract_frame(stored, cal))
    assert len(current) == 1                      # ONE disclosure, not two


def test_na_spelling_variants_share_one_key(tmp_path) -> None:
    # 'None'/'nan'/'<NA>' spellings of a missing announcement date normalize to
    # ONE NA key — a double-content pair under different NA spellings is still
    # ambiguous (refused), not two "distinct" disclosures (codex #351 r3).
    r1 = _income_row("20211231", "0", 100.0)
    r2 = _income_row("20211231", "0", 200.0)
    r1["f_ann_date"] = None
    r2["f_ann_date"] = "nan"
    r1["ann_date"] = None
    r2["ann_date"] = "<NA>"
    ing = FinancialStatementIngestor(_FakeClient([pd.DataFrame([r1, r2])]), tmp_path)
    with pytest.raises(FinancialIngestError, match="DIFFERENT statement contents"):
        ing.ingest("income", "000001.SZ", fetch_batch="b1")


def test_blank_f_ann_date_rows_still_ingest(tmp_path) -> None:
    # f_ann_date is part of the IDENTITY but NOT of the non-blank key columns:
    # a missing announcement date is legitimate (contract layer marks the row
    # unavailable) — the row must still store, not be refused.
    row = _income_row("20211231", "0", 100.0)
    row["f_ann_date"] = None
    ing = FinancialStatementIngestor(_FakeClient([pd.DataFrame([row])]), tmp_path)
    res = ing.ingest("income", "000001.SZ", fetch_batch="b1")
    assert res.rows_new == 1


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


def test_mismatched_ts_code_fails_loud(tmp_path) -> None:
    # provider returns another issuer's rows -> must not be written under the
    # requested issuer's store file (codex #340 r10 P2).
    row = _income_row("20211231", "0", 100.0)
    row["ts_code"] = "600000.SH"  # != requested 000001.SZ
    ing = FinancialStatementIngestor(_FakeClient([pd.DataFrame([row])]), tmp_path)
    with pytest.raises(FinancialIngestError, match="requested"):
        ing.ingest("income", "000001.SZ", fetch_batch="b1")


def test_unknown_endpoint_rejected(tmp_path) -> None:
    ing = FinancialStatementIngestor(_FakeClient([]), tmp_path)
    with pytest.raises(FinancialIngestError, match="unknown financial endpoint"):
        ing.fetch("balance_sheet_typo", "000001.SZ")
