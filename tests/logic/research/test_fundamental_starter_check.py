"""starter-check：三个冻结起步因子的确定性求值端到端（codex #441 r4 P1）。

链路验证不能靠 GP 随机种群"碰巧"构造出 C3（四项收入项 + 五组 Δ +
coalesce 对，远超小跑深度）—— OpenSpec 的"起步三因子跑通链路"义务由
本子命令显式求值三个冻结 AST 来清偿：面板化、真求值器、periods 通路、
终端层对齐掩码全在 GP 同一条路径上。数字只作链路记录，不进任何裁决。
"""
from __future__ import annotations

import json

import pandas as pd
import pytest
import yaml

from scripts.research.fundamental_gp_campaign import (
    _STARTER_EXPRESSIONS,
    main,
)

_TS = ("000000.SZ", "000001.SZ", "000002.SZ")

_INCOME_FIELDS = ("revenue", "total_revenue", "oper_cost", "sell_exp",
                  "admin_exp", "rd_exp", "int_exp", "fin_exp")
_BS_FIELDS = ("total_assets", "total_hldr_eqy_inc_min_int",
              "total_hldr_eqy_exc_min_int", "accounts_receiv", "inventories",
              "prepayment", "accounts_pay", "adv_receipts", "contract_liab")

_STARTER_FIELDS = ("revenue", "oper_cost", "sell_exp", "admin_exp",
                   "total_assets", "accounts_receiv", "inventories",
                   "prepayment", "accounts_pay", "adv_receipts",
                   "contract_liab")


def _row(endpoint, fields, ts, end_date, ann, **data):
    row = {
        "ts_code": ts, "end_date": end_date, "ann_date": ann,
        "f_ann_date": ann, "update_flag": "0",
        "_content_hash": f"h_{endpoint}_{ts}_{end_date}",
        "_fetch_batch": "b1", "_source_endpoint": endpoint,
    }
    for f in fields:
        row[f] = data.get(f, pd.NA)
    return row


def _mk_store(tmp_path):
    """income + balancesheet 双端点、三期（Δ 需相邻期）、三票。

    第三票的 adv_receipts 全 NA 而 contract_liab 有值 —— coalesce 语义
    在真数据形上被走到（不是只在单测里）。
    """
    store = tmp_path / "store"
    for endpoint in ("income", "balancesheet"):
        (store / endpoint).mkdir(parents=True)
    periods = (("20230630", "20230801"), ("20230930", "20231101"),
               ("20231231", "20240201"))
    for i, ts in enumerate(_TS):
        base = 100.0 * (i + 1)
        inc_rows, bs_rows = [], []
        for j, (end, ann) in enumerate(periods):
            inc_rows.append(_row(
                "income", _INCOME_FIELDS, ts, end, ann,
                revenue=base + 10 * j, oper_cost=30.0 + i + j,
                sell_exp=5.0 + j, admin_exp=4.0 + j))
            adv = pd.NA if i == 2 else 8.0 + j          # 第三票走 coalesce
            bs_rows.append(_row(
                "balancesheet", _BS_FIELDS, ts, end, ann,
                total_assets=1000.0 + 50 * j + 100 * i,
                accounts_receiv=20.0 + j, inventories=15.0 + j,
                prepayment=3.0 + j, accounts_pay=12.0 + j,
                adv_receipts=adv, contract_liab=6.0 + j))
        pd.DataFrame(inc_rows).to_parquet(
            store / "income" / f"{ts}.parquet", index=False)
        pd.DataFrame(bs_rows).to_parquet(
            store / "balancesheet" / f"{ts}.parquet", index=False)
    calendar = tmp_path / "days.txt"
    days = pd.date_range("2023-07-01", "2024-06-30", freq="D")
    calendar.write_text(
        "\n".join(d.date().isoformat() for d in days), encoding="utf-8")
    return store, calendar


@pytest.fixture(autouse=True)
def _qlib_namespace_synthetic_panel(monkeypatch):
    import src.factor_mining.miner as miner_mod

    real = miner_mod._build_synthetic_panel
    qlib = tuple(f"SZ{ts[:6]}" for ts in _TS)

    def relabeled(*, n_tickers, n_dates, seed):
        panel, fwd = real(n_tickers=n_tickers, n_dates=n_dates, seed=seed)
        mapping = dict(zip(
            [f"T{i:04d}" for i in range(n_tickers)], qlib[:n_tickers],
            strict=True))
        return ({k: f.rename(columns=mapping) for k, f in panel.items()},
                fwd.rename(columns=mapping))

    monkeypatch.setattr(miner_mod, "_build_synthetic_panel", relabeled)


def _write_config(tmp_path, store, calendar):
    payload = {
        "output_dir": str(tmp_path / "mined"),
        "data": {
            "mode": "synthetic", "synthetic_n_tickers": 3,
            "synthetic_n_dates": 40, "synthetic_seed": 7,
            "fundamental_store_root": str(store),
            "fundamental_calendar_path": str(calendar),
            "fundamental_fields": list(_STARTER_FIELDS),
            "financial_exclusions": [],
        },
        "gp": {"population_size": 6, "n_generations": 1, "max_depth": 3},
    }
    path = tmp_path / "link.yaml"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return path


def test_starter_check_evaluates_all_three_frozen_factors(tmp_path, capsys):
    store, calendar = _mk_store(tmp_path)
    config = _write_config(tmp_path, store, calendar)
    out = tmp_path / "starter_report.json"
    assert main(["starter-check", "--config", str(config),
                 "--out", str(out)]) == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["adjudication_standing"].startswith("none")
    factors = payload["factors"]
    assert set(factors) == set(_STARTER_EXPRESSIONS)
    for name, entry in factors.items():
        # 链路成立的判据：每个因子确有观测被求出来（不是空腿/全 NA），
        # 指标数字本身不裁决。
        assert entry["coverage"] > 0.0, name
        assert entry["n_obs_per_day_min"] >= 1, name
        assert entry["expression"] == _STARTER_EXPRESSIONS[name]
    printed = capsys.readouterr().out
    for name in _STARTER_EXPRESSIONS:
        assert name in printed


def test_starter_check_refuses_to_overwrite_a_record(tmp_path):
    store, calendar = _mk_store(tmp_path)
    config = _write_config(tmp_path, store, calendar)
    out = tmp_path / "starter_report.json"
    out.write_text("{}", encoding="utf-8")
    assert main(["starter-check", "--config", str(config),
                 "--out", str(out)]) == 1
    assert out.read_text(encoding="utf-8") == "{}"


def test_c3_actually_exercises_the_coalesce_leg(tmp_path):
    """第三票 adv_receipts 全 NA：C3 在该票仍有值 = coalesce 腿被走到。"""
    from scripts.research.fundamental_gp_campaign import build_panel_factory
    from src.factor_mining.evaluator import evaluate_factor
    from src.factor_mining.expression import parse_expression
    from src.factor_mining.miner import build_panel_for_data, load_config

    store, calendar = _mk_store(tmp_path)
    config = load_config(_write_config(tmp_path, store, calendar))
    panel, fwd = build_panel_for_data(config.data)
    values, _e, periods = build_panel_factory()(
        config.data, fwd.index, list(fwd.columns))
    result = evaluate_factor(
        parse_expression(_STARTER_EXPRESSIONS["C3_cash_based_OP"]),
        {**panel, **values}, fwd, method="rank", periods=periods)
    third = result.factor_values["SZ000002"]
    assert third.notna().any(), (
        "adv_receipts 全 NA 的票在 C3 上应经 contract_liab 兜住，"
        "全 NA 说明 coalesce 腿没走到")
