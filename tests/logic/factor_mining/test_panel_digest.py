"""工厂输出规范摘要 —— 身份即行为，摘要即身份的载体。

摘要覆盖**全部**影响行为的输出（值、证据、periods 两代）：漏 periods 的话，
两个工厂可以给出相同的值+证据、不同的报告期帧，通过身份校验后在不同的
终端层对齐掩码下裁决同一批表达式。
"""
from __future__ import annotations

import inspect

import numpy as np
import pandas as pd

from src.factor_mining.panel_digest import (
    fundamental_output_sha256,
    periods_fingerprint,
)

_IDX = pd.DatetimeIndex(pd.date_range("2024-01-01", periods=3, freq="D"))
_COLS = ["T0000", "T0001"]


def _triple():
    values = {"$revenue": pd.DataFrame(
        [[1.0, 2.0], [1.0, np.nan], [3.0, 4.0]], index=_IDX, columns=_COLS)}
    evidence = {"$revenue": pd.DataFrame(
        [["2023-11-02", "2023-11-02"], ["2023-11-02", pd.NA],
         ["2023-11-02", "2023-11-03"]], index=_IDX, columns=_COLS,
        dtype="object")}
    periods = {"$revenue": pd.DataFrame(
        [["20230930"] * 2] * 3, index=_IDX, columns=_COLS, dtype="object")}
    return values, evidence, periods


def test_deterministic_across_calls():
    assert fundamental_output_sha256(*_triple()) == \
        fundamental_output_sha256(*_triple())


def test_key_insertion_order_is_irrelevant():
    v, e, p = _triple()
    extra = pd.DataFrame(0.0, index=_IDX, columns=_COLS)
    ep = pd.DataFrame("20230930", index=_IDX, columns=_COLS, dtype="object")
    a = fundamental_output_sha256(
        {"$a": extra, **v}, {"$a": ep, **e}, {"$a": ep, **p})
    b = fundamental_output_sha256(
        {**v, "$a": extra}, {**e, "$a": ep}, {**p, "$a": ep})
    assert a == b


def test_single_value_cell_moves_the_digest():
    base = fundamental_output_sha256(*_triple())
    v, e, p = _triple()
    v["$revenue"].iloc[0, 0] = 999.0
    assert fundamental_output_sha256(v, e, p) != base


def test_single_evidence_cell_moves_the_digest():
    base = fundamental_output_sha256(*_triple())
    v, e, p = _triple()
    e["$revenue"].iloc[0, 0] = "2023-12-29"
    assert fundamental_output_sha256(v, e, p) != base


def test_single_period_cell_moves_the_digest():
    """两工厂值+证据一致、periods 不同 = 不同的对齐掩码 —— 必须换摘要。"""
    base = fundamental_output_sha256(*_triple())
    v, e, p = _triple()
    p["$revenue"].iloc[2, 1] = "20231231"
    assert fundamental_output_sha256(v, e, p) != base


def test_relabeling_moves_the_digest():
    base = fundamental_output_sha256(*_triple())
    v, e, p = _triple()
    v = {"$oper_cost": v.pop("$revenue")}
    e = {"$oper_cost": e.pop("$revenue")}
    p = {"$oper_cost": p.pop("$revenue")}
    assert fundamental_output_sha256(v, e, p) != base


def test_geometry_moves_the_digest():
    base = fundamental_output_sha256(*_triple())
    v, e, p = _triple()
    shifted = _IDX + pd.Timedelta(days=1)
    v = {k: f.set_axis(shifted) for k, f in v.items()}
    e = {k: f.set_axis(shifted) for k, f in e.items()}
    p = {k: f.set_axis(shifted) for k, f in p.items()}
    assert fundamental_output_sha256(v, e, p) != base


def test_nan_payload_bits_are_canonicalized():
    """不同位型的 NaN 是同一个"无值" —— 摘要不得分裂它们。"""
    v, e, p = _triple()
    base = fundamental_output_sha256(v, e, p)
    weird_nan = np.frombuffer(
        np.uint64(0x7FF8000000000001).tobytes(), dtype="float64")[0]
    v2, e2, p2 = _triple()
    v2["$revenue"].iloc[1, 1] = weird_nan
    assert fundamental_output_sha256(v2, e2, p2) == base


def test_periods_fingerprint_none_stays_none():
    assert periods_fingerprint(None) is None
    _v, _e, p = _triple()
    fp1 = periods_fingerprint(p)
    p["$revenue"].iloc[0, 0] = "20231231"
    assert periods_fingerprint(p) != fp1


def test_panel_digest_does_not_import_qlib_or_pit_directly():
    import src.factor_mining.panel_digest as mod

    src = inspect.getsource(mod)
    assert "from qlib" not in src
    assert "qlib.data" not in src
    assert "qlib.init" not in src
    assert "from src.pit" not in src
    assert "import src.pit" not in src
    assert "from src.research" not in src
    assert "import src.research" not in src
