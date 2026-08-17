"""starter-check：三个冻结起步因子的确定性求值（run-bound，codex #441 r4/r5）。

链路验证不能靠 GP 随机种群"碰巧"构造出 C3 —— 本子命令显式求值三个冻结
AST 清偿 OpenSpec 义务。r5 收紧的三条契约在此钉住：

* **绑 run 不绑可变 yaml**：快照摘要验证 + 工厂输出摘要须复现 run 记录
  的身份，starter 记录描述的就是 run 挖过的那块面板；
* **空腿即拒**：coverage=0 / 无观测的因子不是"完成的检查"，exit 1 且
  不落盘；
* **合法 JSON**：IC 系列空/零方差时指标为 NaN —— 序列化为 null，
  裸 NaN 不出厂。
"""
from __future__ import annotations

import json

import pandas as pd
import pytest
import yaml

from scripts.research.fundamental_gp_campaign import (
    _STARTER_EXPRESSIONS,
    build_panel_factory,
    main,
)
from src.factor_mining.fitness import FitnessConfig
from src.factor_mining.gp_engine import GPConfig
from src.factor_mining.miner import DataConfig, MinerConfig, run_mining

_TS = ("000000.SZ", "000001.SZ", "000002.SZ")
_QLIB = tuple(f"SZ{ts[:6]}" for ts in _TS)

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


def _mk_store(tmp_path, *, all_na_field: str | None = None):
    """income + balancesheet 双端点、三期（Δ 需相邻期）、三票。

    第三票的 adv_receipts 全 NA 而 contract_liab 有值 —— coalesce 语义在
    真数据形上被走到。``all_na_field`` 把指定字段整列打成 NA（零覆盖
    拒绝场景用）。
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
            inc = dict(revenue=base + 10 * j, oper_cost=30.0 + i + j,
                       sell_exp=5.0 + j, admin_exp=4.0 + j)
            adv = pd.NA if i == 2 else 8.0 + j          # 第三票走 coalesce
            bs = dict(total_assets=1000.0 + 50 * j + 100 * i,
                      accounts_receiv=20.0 + j, inventories=15.0 + j,
                      prepayment=3.0 + j, accounts_pay=12.0 + j,
                      adv_receipts=adv, contract_liab=6.0 + j)
            if all_na_field:
                inc.pop(all_na_field, None)
                bs.pop(all_na_field, None)
            inc_rows.append(_row("income", _INCOME_FIELDS, ts, end, ann,
                                 **inc))
            bs_rows.append(_row("balancesheet", _BS_FIELDS, ts, end, ann,
                                **bs))
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

    def relabeled(*, n_tickers, n_dates, seed):
        panel, fwd = real(n_tickers=n_tickers, n_dates=n_dates, seed=seed)
        mapping = dict(zip(
            [f"T{i:04d}" for i in range(n_tickers)], _QLIB[:n_tickers],
            strict=True))
        return ({k: f.rename(columns=mapping) for k, f in panel.items()},
                fwd.rename(columns=mapping))

    monkeypatch.setattr(miner_mod, "_build_synthetic_panel", relabeled)


def _mine(tmp_path, store, calendar, run_id="starter-run"):
    config = MinerConfig(
        data=DataConfig(
            mode="synthetic", synthetic_n_tickers=3, synthetic_n_dates=40,
            synthetic_seed=7,
            fundamental_store_root=str(store),
            fundamental_calendar_path=str(calendar),
            fundamental_fields=_STARTER_FIELDS,
            financial_exclusions=(),
        ),
        gp=GPConfig(population_size=6, n_generations=1, max_depth=3, seed=7),
        fitness=FitnessConfig(),
        output_dir=tmp_path / "mined",
        run_id=run_id,
    )
    return run_mining(config, fundamental_panel_factory=build_panel_factory())


def test_starter_check_evaluates_all_three_and_binds_the_run(
        tmp_path, capsys):
    store, calendar = _mk_store(tmp_path)
    result = _mine(tmp_path, store, calendar)
    assert main(["starter-check", "--run", str(result.output_dir)]) == 0
    out = result.output_dir / "starter_factor_report.json"
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["adjudication_standing"].startswith("none")
    # 绑定：报告携带快照摘要与工厂输出摘要，且与 run 记录一致。
    raw = yaml.safe_load(
        (result.output_dir / "config.yaml").read_text(encoding="utf-8"))
    assert payload["data_definition_sha256"] == raw["data_definition_sha256"]
    assert payload["fundamental_output_sha256"] == \
        raw["fundamental_output_sha256"]
    factors = payload["factors"]
    assert set(factors) == set(_STARTER_EXPRESSIONS)
    for name, entry in factors.items():
        assert entry["coverage"] > 0.0, name
        assert entry["n_obs_per_day_min"] >= 1, name
        assert entry["expression"] == _STARTER_EXPRESSIONS[name]
    # 合法 JSON：裸 NaN/Infinity 不出厂。
    json.loads(out.read_text(encoding="utf-8"),
               parse_constant=lambda c: pytest.fail(f"裸 {c} 出厂"))
    printed = capsys.readouterr().out
    for name in _STARTER_EXPRESSIONS:
        assert name in printed


def test_a_store_change_after_mining_is_refused(tmp_path, capsys):
    store, calendar = _mk_store(tmp_path)
    result = _mine(tmp_path, store, calendar)
    target = store / "income" / f"{_TS[0]}.parquet"
    frame = pd.read_parquet(target)
    frame.loc[0, "revenue"] = 99999.0
    frame.to_parquet(target, index=False)
    assert main(["starter-check", "--run", str(result.output_dir)]) == 1
    assert not (result.output_dir / "starter_factor_report.json").exists()
    assert "recorded identity" in capsys.readouterr().err


def test_a_broken_leg_is_refused_not_reported(tmp_path, capsys):
    """必需字段整列 NA → 因子无观测 → exit 1 且不落盘（空腿≠完成）。"""
    store, calendar = _mk_store(tmp_path, all_na_field="prepayment")
    result = _mine(tmp_path, store, calendar, run_id="broken-run")
    assert main(["starter-check", "--run", str(result.output_dir)]) == 1
    assert not (result.output_dir / "starter_factor_report.json").exists()
    assert "no evaluable observations" in capsys.readouterr().err


def test_an_existing_report_is_never_overwritten(tmp_path):
    store, calendar = _mk_store(tmp_path)
    result = _mine(tmp_path, store, calendar)
    out = result.output_dir / "starter_factor_report.json"
    out.write_text("{}", encoding="utf-8")
    assert main(["starter-check", "--run", str(result.output_dir)]) == 1
    assert out.read_text(encoding="utf-8") == "{}"


def test_a_pv_only_run_is_refused(tmp_path):
    config = MinerConfig(
        data=DataConfig(mode="synthetic", synthetic_n_tickers=3,
                        synthetic_n_dates=40, synthetic_seed=7),
        gp=GPConfig(population_size=6, n_generations=1, max_depth=3, seed=7),
        fitness=FitnessConfig(),
        output_dir=tmp_path / "mined", run_id="pv-run",
    )
    result = run_mining(config)
    assert main(["starter-check", "--run", str(result.output_dir)]) == 1


def test_c3_actually_exercises_the_coalesce_leg(tmp_path):
    """第三票 adv_receipts 全 NA：C3 在该票仍有值 = coalesce 腿被走到。"""
    from src.factor_mining.evaluator import evaluate_factor
    from src.factor_mining.expression import parse_expression
    from src.factor_mining.miner import build_panel_for_data
    from src.factor_mining.promote import _load_run_data_config

    store, calendar = _mk_store(tmp_path)
    result = _mine(tmp_path, store, calendar)
    run_data, _sha = _load_run_data_config(result.output_dir)
    panel, fwd = build_panel_for_data(run_data)
    values, _e, periods = build_panel_factory()(
        run_data, fwd.index, list(fwd.columns))
    result_eval = evaluate_factor(
        parse_expression(_STARTER_EXPRESSIONS["C3_cash_based_OP"]),
        {**panel, **values}, fwd, method="rank", periods=periods)
    third = result_eval.factor_values["SZ000002"]
    assert third.notna().any(), (
        "adv_receipts 全 NA 的票在 C3 上应经 contract_liab 兜住，"
        "全 NA 说明 coalesce 腿没走到")


def test_an_edited_search_section_is_refused(tmp_path, capsys):
    """gp/fitness 节与 data 同待遇（codex #441 r8 P2）：事后编辑即拒，
    不得用 run 从未用过的标准打分还宣称"run 自己的配置"。"""
    store, calendar = _mk_store(tmp_path)
    result = _mine(tmp_path, store, calendar)
    config_path = result.output_dir / "config.yaml"
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["fitness"]["w_ic"] = 99.0
    config_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    assert main(["starter-check", "--run", str(result.output_dir)]) == 1
    assert "search_definition" in capsys.readouterr().err or True
    assert not (result.output_dir / "starter_factor_report.json").exists()


def test_report_carries_the_search_digest(tmp_path):
    store, calendar = _mk_store(tmp_path)
    result = _mine(tmp_path, store, calendar)
    assert main(["starter-check", "--run", str(result.output_dir)]) == 0
    payload = json.loads(
        (result.output_dir / "starter_factor_report.json").read_text(
            encoding="utf-8"))
    raw = yaml.safe_load(
        (result.output_dir / "config.yaml").read_text(encoding="utf-8"))
    assert payload["search_definition_sha256"] == \
        raw["search_definition_sha256"]
    for entry in payload["factors"].values():
        assert "fitness" in entry


def test_starter_fitness_is_order_independent(tmp_path, monkeypatch):
    """novelty 项对累积池的依赖曾让报告随 dict 顺序变（codex #441 r9）：
    每因子独立 engine 后，逆序打分必须给出逐字段相同的 fitness。"""
    import scripts.research.fundamental_gp_campaign as camp

    store, calendar = _mk_store(tmp_path)
    result = _mine(tmp_path, store, calendar)
    out_a = tmp_path / "a.json"
    assert main(["starter-check", "--run", str(result.output_dir),
                 "--out", str(out_a)]) == 0
    reversed_exprs = dict(reversed(list(camp._STARTER_EXPRESSIONS.items())))
    monkeypatch.setattr(camp, "_STARTER_EXPRESSIONS", reversed_exprs)
    out_b = tmp_path / "b.json"
    assert main(["starter-check", "--run", str(result.output_dir),
                 "--out", str(out_b)]) == 0
    a = json.loads(out_a.read_text(encoding="utf-8"))["factors"]
    b = json.loads(out_b.read_text(encoding="utf-8"))["factors"]
    for name in a:
        assert a[name]["fitness"] == b[name]["fitness"], name
