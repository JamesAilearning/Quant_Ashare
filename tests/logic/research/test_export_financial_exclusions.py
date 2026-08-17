"""导出器守卫：空推导拒绝落盘、既有产物拒绝覆盖、正常路径契约。

空名单分支（codex #441 r1 P2 ①）的回归：industry 缺失/改标签时推导为
空 —— 那是参考数据不完整，不是"市场没有金融股"；写出签收产物就等于让
不完整数据变成"批准的、谁也不排除的配置"。exit 1 且**无产物**。
"""
from __future__ import annotations

import json

import pandas as pd

from scripts.research.export_financial_exclusions import main

_COLS = ("ts_code", "name", "industry", "area", "market", "list_status",
         "list_date", "delist_date", "curr_type", "symbol", "snapshot_date")


def _snapshot(rows) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=["ts_code", "name", "industry"])
    for col in _COLS:
        if col not in frame.columns:
            frame[col] = "20260817" if col == "snapshot_date" else pd.NA
    return frame[list(_COLS)]


def _write_snapshots(tmp_path, active_rows, delisted_rows=()):
    _snapshot(active_rows).to_parquet(
        tmp_path / "active_stocks.parquet", index=False)
    _snapshot(delisted_rows or []).to_parquet(
        tmp_path / "delisted_stocks.parquet", index=False)


def test_an_empty_derivation_refuses_and_writes_nothing(tmp_path, capsys):
    """industry 全非金融（或缺失）→ 空推导 → exit 1、产物不存在。"""
    _write_snapshots(tmp_path, [
        ("000004.SZ", "某科技", "软件"),
        ("000005.SZ", "某制造", pd.NA),
    ])
    out = tmp_path / "exclusions.json"
    rc = main(["--snapshot-dir", str(tmp_path), "--out", str(out)])
    assert rc == 1
    assert not out.exists()
    assert "EMPTY" in capsys.readouterr().err


def test_a_normal_export_carries_the_signoff_contract(tmp_path):
    _write_snapshots(
        tmp_path,
        [("000001.SZ", "平安银行", "银行"), ("000004.SZ", "某科技", "软件")],
        [("600030.SH", "中信证券", "证券")],
    )
    out = tmp_path / "exclusions.json"
    assert main(["--snapshot-dir", str(tmp_path), "--out", str(out)]) == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["ts_codes"] == ["000001.SZ", "600030.SH"]
    assert payload["qlib_tickers"] == ["SZ000001", "SH600030"]
    assert payload["n_excluded"] == 2
    assert payload["purpose"] == "financial-exclusions-for-sign-off"
    # 退市覆盖缺口是签收报告的一部分，必须在产物里。
    assert payload["n_delisted_rows"] == 1
    assert payload["n_delisted_with_industry"] == 1


def test_an_existing_artifact_is_never_overwritten(tmp_path):
    """已存在的产物可能正在被审 —— 第二次导出不得改写它。"""
    _write_snapshots(tmp_path, [("000001.SZ", "平安银行", "银行")])
    out = tmp_path / "exclusions.json"
    out.write_text("{}", encoding="utf-8")
    assert main(["--snapshot-dir", str(tmp_path), "--out", str(out)]) == 1
    assert out.read_text(encoding="utf-8") == "{}"


def test_a_missing_snapshot_refuses(tmp_path):
    rc = main(["--snapshot-dir", str(tmp_path),
               "--out", str(tmp_path / "x.json")])
    assert rc == 1
    assert not (tmp_path / "x.json").exists()
