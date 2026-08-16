"""注入缝端到端：真工厂（真桥 + 微型 store）走 mine → promote 全链。

缝的规格（提案 #427 r11-r18 冻结）：
* 工厂与基本面配置**必须同来同往**（两个方向的缺席都是接线失败）；
* 工厂身份 = 受信代码对其**输出**（值、证据、periods）算的摘要，绝不自报；
* 晋升重演：入口指纹（take#1）→ 原窗行为重算 → 求值 → take#2 → 才碰生产；
* 调包工厂在每个配置值都对得上的情况下，必须被**行为**摘要抓住。

端到端测试必须走真工厂 —— 缝的替身测不出缝的契约。
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
import yaml

from scripts.research.fundamental_gp_campaign import build_panel_factory
from src.factor_mining.expression import parse_expression
from src.factor_mining.factor_pool import FactorPool, PoolEntry
from src.factor_mining.fitness import FitnessConfig
from src.factor_mining.gp_engine import GPConfig
from src.factor_mining.miner import (
    DataConfig,
    MinerConfig,
    _apply_financial_exclusions,
    run_mining,
)
from src.factor_mining.panel_digest import fundamental_output_sha256
from src.factor_mining.promote import (
    PromotionConfig,
    PromotionError,
    _load_run_data_config,
    promote_run,
)
from src.factor_mining.validator import ValidationCriteria

# --- 微型基本面输入（真 store、真日历文件）---------------------------------
#
# view 严格校验 instrument 命名空间（ts_code 或 qlib 形，拒绝瞎猜），而合成
# pv 面板的 ticker 是 "T0000" 形 —— 二者天然不相容。缝的测试对象是工厂与
# 身份链，不是合成面板的取名，所以这里把合成面板的列名换成真形 qlib 码
# （SZ000000...），store 相应以 ts_code 落盘。工厂全程走真桥。

_QLIB_TICKERS = ("SZ000000", "SZ000001")
_TS_CODES = ("000000.SZ", "000001.SZ")

_FIELDS = ("revenue", "total_revenue", "oper_cost", "sell_exp",
           "admin_exp", "rd_exp", "int_exp", "fin_exp")


@pytest.fixture(autouse=True)
def _synthetic_panel_in_qlib_namespace(monkeypatch):
    import src.factor_mining.miner as miner_mod

    real = miner_mod._build_synthetic_panel

    def relabeled(*, n_tickers, n_dates, seed):
        assert n_tickers <= len(_QLIB_TICKERS)
        panel, fwd = real(n_tickers=n_tickers, n_dates=n_dates, seed=seed)
        mapping = dict(zip(
            [f"T{i:04d}" for i in range(n_tickers)],
            _QLIB_TICKERS[:n_tickers], strict=True))
        panel = {k: f.rename(columns=mapping) for k, f in panel.items()}
        return panel, fwd.rename(columns=mapping)

    monkeypatch.setattr(miner_mod, "_build_synthetic_panel", relabeled)


def _row(ts, end_date, ann, **data):
    row = {
        "ts_code": ts, "end_date": end_date, "ann_date": ann,
        "f_ann_date": ann, "update_flag": "0",
        "_content_hash": f"h_{ts}_{end_date}", "_fetch_batch": "b1",
        "_source_endpoint": "income",
    }
    for f in _FIELDS:
        row[f] = data.get(f, pd.NA)
    return row


def _mk_fundamental_inputs(tmp_path: Path) -> tuple[Path, Path]:
    """income store（三期，含 prior 所需的相邻期）+ 日历文件。"""
    store = tmp_path / "fund_store"
    inc = store / "income"
    inc.mkdir(parents=True)
    for i, ts in enumerate(_TS_CODES):
        base = 100.0 * (i + 1)
        pd.DataFrame([
            _row(ts, "20230630", "20230801", revenue=base),
            _row(ts, "20230930", "20231101", revenue=base + 10),
            _row(ts, "20231231", "20240201", revenue=base + 30),
        ]).to_parquet(inc / f"{ts}.parquet", index=False)
    calendar = tmp_path / "trading_days.txt"
    days = pd.date_range("2023-07-01", "2024-06-30", freq="D")
    calendar.write_text(
        "\n".join(d.date().isoformat() for d in days), encoding="utf-8")
    return store, calendar


def _config(tmp_path: Path, store: Path, calendar: Path,
            run_id: str = "seam-run", **data_over) -> MinerConfig:
    data_kw = dict(
        mode="synthetic", synthetic_n_tickers=2, synthetic_n_dates=40,
        synthetic_seed=7,
        fundamental_store_root=str(store),
        fundamental_calendar_path=str(calendar),
        fundamental_fields=("revenue",),
        financial_exclusions=(),
    )
    data_kw.update(data_over)
    return MinerConfig(
        data=DataConfig(**data_kw),
        gp=GPConfig(population_size=6, n_generations=1, max_depth=3, seed=7),
        fitness=FitnessConfig(),
        output_dir=tmp_path / "mined",
        run_id=run_id,
    )


@pytest.fixture
def mined(tmp_path):
    """一次真挖掘（真工厂），供多个断言共用。"""
    store, calendar = _mk_fundamental_inputs(tmp_path)
    config = _config(tmp_path, store, calendar)
    result = run_mining(
        config, fundamental_panel_factory=build_panel_factory())
    return tmp_path, store, calendar, config, result


# --- 挖掘侧 -----------------------------------------------------------------

def test_mining_records_the_behavioral_identity(mined):
    tmp_path, _store, _calendar, config, result = mined
    raw = yaml.safe_load(
        (result.output_dir / "config.yaml").read_text(encoding="utf-8"))
    assert raw["fundamental_store_sha256"]
    assert raw["fundamental_calendar_sha256"]
    recorded = raw["fundamental_output_sha256"]
    assert recorded
    binding = json.loads(
        (result.output_dir / "fundamental_binding.json").read_text(
            encoding="utf-8"))
    assert binding["output_sha256"] == recorded
    assert len(binding["trade_dates"]) == 40
    assert binding["instruments"] == list(_QLIB_TICKERS)
    # 行为重算必须逐位复现 —— 这是晋升侧身份校验的全部根据。
    factory = build_panel_factory()
    dates = pd.DatetimeIndex([pd.Timestamp(d) for d in binding["trade_dates"]])
    got = fundamental_output_sha256(
        *factory(config.data, dates, binding["instruments"]))
    assert got == recorded


def test_mining_panel_actually_carried_the_fundamental_terminals(mined):
    """缝不是装饰：报告期 provenance 与 prior 键都进了求值面板。

    直接以工厂输出为证 —— values/periods 键集含 $revenue 与 $revenue__prior，
    且窗口内确有被服务的值（不是全 NA 的空腿）。
    """
    _tmp, _store, _calendar, config, result = mined
    binding = json.loads(
        (result.output_dir / "fundamental_binding.json").read_text(
            encoding="utf-8"))
    dates = pd.DatetimeIndex([pd.Timestamp(d) for d in binding["trade_dates"]])
    values, _evidence, periods = build_panel_factory()(
        config.data, dates, binding["instruments"])
    assert set(values) == {"$revenue", "$revenue__prior"}
    assert set(periods) == set(values)
    assert values["$revenue"].notna().any().any()
    assert values["$revenue__prior"].notna().any().any()


def test_factory_without_fundamental_config_is_refused(tmp_path):
    config = _config(tmp_path, tmp_path, tmp_path,
                     fundamental_store_root="",
                     fundamental_calendar_path="",
                     fundamental_fields=())
    with pytest.raises(ValueError, match="records no fundamental inputs"):
        run_mining(config, fundamental_panel_factory=build_panel_factory())
    assert not (tmp_path / "mined").exists()   # 早于任何目录预留


def test_fundamental_config_without_factory_is_refused(tmp_path):
    store, calendar = _mk_fundamental_inputs(tmp_path)
    config = _config(tmp_path, store, calendar)
    with pytest.raises(ValueError, match="no fundamental_panel_factory"):
        run_mining(config)
    assert not (tmp_path / "mined").exists()


def test_fundamental_config_requires_fields_and_calendar(tmp_path):
    store, calendar = _mk_fundamental_inputs(tmp_path)
    with pytest.raises(ValueError, match="fundamental_fields"):
        run_mining(
            _config(tmp_path, store, calendar, fundamental_fields=()),
            fundamental_panel_factory=build_panel_factory())
    with pytest.raises(ValueError, match="fundamental_calendar_path"):
        run_mining(
            _config(tmp_path, store, calendar,
                    fundamental_calendar_path=""),
            fundamental_panel_factory=build_panel_factory())


def test_geometry_violation_is_refused(tmp_path):
    store, calendar = _mk_fundamental_inputs(tmp_path)
    real = build_panel_factory()

    def dropped_column(data, trade_dates, instruments):
        values, evidence, periods = real(data, trade_dates, instruments)
        return (
            {k: f.iloc[:, :-1] for k, f in values.items()},
            {k: f.iloc[:, :-1] for k, f in evidence.items()},
            {k: f.iloc[:, :-1] for k, f in periods.items()},
        )

    with pytest.raises(RuntimeError, match="geometry"):
        run_mining(_config(tmp_path, store, calendar),
                   fundamental_panel_factory=dropped_column)


def test_key_collision_with_the_pv_panel_is_refused(tmp_path):
    store, calendar = _mk_fundamental_inputs(tmp_path)
    real = build_panel_factory()

    def shadowing(data, trade_dates, instruments):
        values, evidence, periods = real(data, trade_dates, instruments)
        values = {"$close": next(iter(values.values())), **values}
        evidence = {"$close": next(iter(evidence.values())), **evidence}
        periods = {"$close": next(iter(periods.values())), **periods}
        return values, evidence, periods

    with pytest.raises(RuntimeError, match="collide"):
        run_mining(_config(tmp_path, store, calendar),
                   fundamental_panel_factory=shadowing)


def test_store_refresh_during_mining_is_refused(tmp_path, monkeypatch):
    store, calendar = _mk_fundamental_inputs(tmp_path)
    import src.factor_mining.miner as miner_mod

    real_fp = miner_mod.fundamental_binding_fingerprints
    calls = {"n": 0}

    def unstable(data):
        calls["n"] += 1
        fp = dict(real_fp(data))
        if calls["n"] > 1:
            fp["fundamental_store_sha256"] = "refreshed-mid-run"
        return fp

    monkeypatch.setattr(
        miner_mod, "fundamental_binding_fingerprints", unstable)
    with pytest.raises(RuntimeError, match="changed while mining"):
        run_mining(_config(tmp_path, store, calendar),
                   fundamental_panel_factory=build_panel_factory())
    # 拒绝发生在任何 artifact 持久化之前：run 目录只剩空预留被回收。
    runs = tmp_path / "mined" / "runs"
    assert not runs.exists() or not any(runs.iterdir())


def test_financial_exclusions_leave_the_coverage_denominator():
    mask = pd.DataFrame(
        True,
        index=pd.date_range("2024-01-01", periods=3, freq="D"),
        columns=["SZ000000", "SZ000001", "SZ000002"],
    )
    cut = _apply_financial_exclusions(mask, ("SZ000001", "NOT_A_MEMBER"))
    assert not cut["SZ000001"].any()       # 被排除者从分母消失
    assert cut["SZ000000"].all() and cut["SZ000002"].all()
    assert mask["SZ000001"].all()          # 原掩码不被原地改写
    assert _apply_financial_exclusions(mask, ()) is mask
    assert _apply_financial_exclusions(None, ("SZ000001",)) is None


# --- 晋升侧 -----------------------------------------------------------------

def _pv_entry(text: str, fitness: float = 1.0) -> PoolEntry:
    expr = parse_expression(text)
    return PoolEntry(
        expr=expr, fitness=fitness, ic_mean=0.05, ic_std=0.10, ir=0.5,
        rank_ic_mean=0.04, rank_ic_std=0.08, rank_ir=0.5,
        turnover_daily=0.10, coverage=0.95, n_obs_per_day_min=2,
        expr_size=2, expr_hash=hash(expr), method="rank",
    )


def _overwrite_pool(run_dir: Path, *texts: str) -> None:
    """晋升侧断言不依赖 GP 随机产物 —— 池是受控输入，配置身份不动。"""
    pool = FactorPool()
    for i, t in enumerate(texts):
        pool.add(_pv_entry(t, fitness=2.0 - 0.2 * i))
    pool.save(run_dir)


def _promotion_config(tmp_path: Path, run_dir: Path,
                      version: str = "v1") -> PromotionConfig:
    data, sha = _load_run_data_config(run_dir)
    return PromotionConfig(
        run_dir=run_dir,
        production_dir=tmp_path / "production",
        version=version,
        criteria=ValidationCriteria(
            is_oos_split_date="2024-02-02",
            min_oos_ir=0.0, min_oos_rank_ic_mean=-1.0,
            max_pool_correlation=0.99, min_obs_per_segment=2,
        ),
        data=data,
        data_definition_sha256=sha,
    )


def test_promotion_with_the_same_factory_completes(mined):
    tmp_path, _store, _calendar, _config_, result = mined
    _overwrite_pool(result.output_dir, "cs_rank($volume)")
    config = _promotion_config(tmp_path, result.output_dir)
    report = promote_run(
        config, fundamental_panel_factory=build_panel_factory())
    assert report.output_dir is not None
    payload = json.loads(
        (report.output_dir / "promotion_report.json").read_text(
            encoding="utf-8"))
    assert payload["fundamental_output_sha256_mined"] == \
        payload["fundamental_output_sha256_effective"]
    assert payload["fundamental_store_sha256"]


def test_promotion_hands_periods_to_both_adjudication_calls(
        mined, monkeypatch):
    """接线断言：validate_pool 与 filter_correlated 都收到了 provenance。

    periods 的**效果**（遮蔽改变指标）由 validator 的公开入口测试钉住
    （#437）；这里钉的是晋升入口没有把它掉在地上 —— 两处任一为 None，
    裁决就回到未遮蔽值。
    """
    tmp_path, _store, _calendar, _config_, result = mined
    _overwrite_pool(result.output_dir, "cs_rank($volume)")
    import src.factor_mining.promote as promote_mod

    seen = {}
    real_validate = promote_mod.validate_pool
    real_filter = promote_mod.filter_correlated

    def spy_validate(pool, panel, fwd, criteria, periods=None):
        seen["validate"] = periods
        return real_validate(pool, panel, fwd, criteria, periods=periods)

    def spy_filter(results, panel, criteria, pool, periods=None):
        seen["filter"] = periods
        return real_filter(results, panel, criteria, pool, periods=periods)

    monkeypatch.setattr(promote_mod, "validate_pool", spy_validate)
    monkeypatch.setattr(promote_mod, "filter_correlated", spy_filter)
    promote_run(_promotion_config(tmp_path, result.output_dir, "v2"),
                fundamental_panel_factory=build_panel_factory())
    assert seen["validate"] is not None
    assert seen["filter"] is not None
    assert "$revenue" in seen["validate"]
    assert "$revenue__prior" in seen["validate"]


def test_a_swapped_factory_is_refused_by_behavior(mined):
    """调包场景：配置值、data digest、store 指纹全对，只有 callable 不同。"""
    tmp_path, _store, _calendar, _config_, result = mined
    _overwrite_pool(result.output_dir, "cs_rank($volume)")
    real = build_panel_factory()

    def blind_builder(data, trade_dates, instruments):
        values, evidence, periods = real(data, trade_dates, instruments)
        # 对公告日"盲目"的构建器：periods 抄成同一期 —— 值与证据不动。
        periods = {k: f.where(f.isna(), "20230930")
                   for k, f in periods.items()}
        return values, evidence, periods

    with pytest.raises(PromotionError, match="does not reproduce"):
        promote_run(_promotion_config(tmp_path, result.output_dir, "v3"),
                    fundamental_panel_factory=blind_builder, dry_run=True)


def test_promotion_without_the_factory_is_refused(mined):
    tmp_path, _store, _calendar, _config_, result = mined
    with pytest.raises(PromotionError, match="no fundamental_panel_factory"):
        promote_run(_promotion_config(tmp_path, result.output_dir, "v4"))


def test_factory_on_a_price_volume_run_is_refused(tmp_path):
    config = _config(tmp_path, tmp_path, tmp_path,
                     fundamental_store_root="",
                     fundamental_calendar_path="",
                     fundamental_fields=())
    result = run_mining(config)
    with pytest.raises(PromotionError, match="records no fundamental leg"):
        promote_run(_promotion_config(tmp_path, result.output_dir),
                    fundamental_panel_factory=build_panel_factory())


def test_a_run_predating_identity_recording_is_refused(mined):
    tmp_path, _store, _calendar, _config_, result = mined
    (result.output_dir / "fundamental_binding.json").unlink()
    with pytest.raises(PromotionError, match="predates identity recording"):
        promote_run(_promotion_config(tmp_path, result.output_dir, "v5"),
                    fundamental_panel_factory=build_panel_factory())


def test_store_tampered_after_mining_is_refused(mined):
    tmp_path, store, _calendar, _config_, result = mined
    target = store / "income" / f"{_TS_CODES[0]}.parquet"
    frame = pd.read_parquet(target)
    frame.loc[0, "revenue"] = 12345.0
    frame.to_parquet(target, index=False)
    with pytest.raises(PromotionError, match="fundamental_store_sha256"):
        promote_run(_promotion_config(tmp_path, result.output_dir, "v6"),
                    fundamental_panel_factory=build_panel_factory())


def test_refresh_during_promotion_is_refused(mined):
    """take#2：求值窗口内的刷新在碰生产之前被拒。

    第二次工厂调用（effective 面板构建）时篡改日历文件 —— 身份重算
    （第一次调用）已过、take#1 已取，唯一还站岗的就是 take#2。
    """
    tmp_path, _store, calendar, _config_, result = mined
    _overwrite_pool(result.output_dir, "cs_rank($volume)")
    real = build_panel_factory()
    calls = {"n": 0}

    def refreshing(data, trade_dates, instruments):
        calls["n"] += 1
        out = real(data, trade_dates, instruments)
        if calls["n"] == 2:
            with open(calendar, "a", encoding="utf-8") as fh:
                fh.write("\n2024-07-01")
        return out

    with pytest.raises(PromotionError, match="changed while promotion"):
        promote_run(_promotion_config(tmp_path, result.output_dir, "v7"),
                    fundamental_panel_factory=refreshing)
    assert not (tmp_path / "production" / "v7").exists()


def test_fundamental_survivors_still_hit_the_production_boundary(
        mined, monkeypatch):
    """PR-4 打通了晋升路径，但生产物化消费者仍未接线 —— 写盘拒绝必须兜底。

    「基本面条目通过校验」这个前置条件在微型合成面板上不可确定性构造
    （2 名截面的 IC 退化），所以用 spy 把 passes 固定为 True —— **拒绝
    逻辑本身走真代码**：真工厂、真晋升路径、真 survivor pool 组装。
    """
    tmp_path, _store, _calendar, _config_, result = mined
    _overwrite_pool(result.output_dir,
                    "cs_rank(div_safe($revenue, $revenue__prior))")
    import dataclasses

    import src.factor_mining.promote as promote_mod

    real_filter = promote_mod.filter_correlated

    def all_pass(results, panel, criteria, pool, periods=None):
        out = real_filter(results, panel, criteria, pool, periods=periods)
        return [dataclasses.replace(r, passes=True, reasons=()) for r in out]

    monkeypatch.setattr(promote_mod, "filter_correlated", all_pass)
    with pytest.raises(PromotionError, match="FUNDAMENTAL pool"):
        promote_run(_promotion_config(tmp_path, result.output_dir, "v8"),
                    fundamental_panel_factory=build_panel_factory())
    # 被拒的晋升完全没碰生产：目录不存在，版本号未被吃掉。
    assert not (tmp_path / "production" / "v8").exists()


# --- 扩窗预授权基线（合成路径不允许扩窗，故直接测校验器）--------------------

def _fake_fwd():
    idx = pd.DatetimeIndex(pd.date_range("2024-01-01", periods=4, freq="D"))
    return pd.DataFrame(0.0, index=idx, columns=list(_QLIB_TICKERS))


def _baseline_payload(config, fwd, output_sha):
    from src.factor_mining.miner import data_definition_sha256
    return {
        "purpose": "fundamental-extension-baseline",
        "data_definition_sha256": data_definition_sha256(config.data),
        "trade_dates": [str(d.date()) for d in fwd.index],
        "instruments": [str(c) for c in fwd.columns],
        "output_sha256": output_sha,
    }


def _baseline_config(tmp_path, baseline_path=None):
    return PromotionConfig(
        run_dir=tmp_path, production_dir=tmp_path / "prod", version="v1",
        criteria=ValidationCriteria(is_oos_split_date="2024-01-03"),
        data=DataConfig(mode="pit"),
        data_definition_sha256="irrelevant",
        validation_end_date="2024-01-04",
        fundamental_baseline_path=baseline_path,
    )


def test_extension_without_a_baseline_is_refused(tmp_path):
    from src.factor_mining.promote import (
        _verify_fundamental_extension_baseline,
    )
    with pytest.raises(PromotionError, match="pre-authorized"):
        _verify_fundamental_extension_baseline(
            _baseline_config(tmp_path), _fake_fwd(), "sha-x")


def test_a_valid_baseline_passes_and_each_binding_is_load_bearing(tmp_path):
    from src.factor_mining.promote import (
        _verify_fundamental_extension_baseline,
    )
    fwd = _fake_fwd()
    path = tmp_path / "baseline.json"
    config = _baseline_config(tmp_path, path)
    good = _baseline_payload(config, fwd, "sha-x")
    path.write_text(json.dumps(good), encoding="utf-8")
    assert _verify_fundamental_extension_baseline(
        config, fwd, "sha-x") == "sha-x"

    # 逐一破坏每个绑定维度 —— 任一失守，调包工厂就能在扩窗日期上发散。
    for mutate, pattern in (
        (lambda b: {**b, "purpose": "something-else"}, "purpose"),
        (lambda b: {**b, "data_definition_sha256": "other"},
         r"different\s+effective data definition"),
        (lambda b: {**b, "trade_dates": b["trade_dates"][:-1]}, "geometry"),
        (lambda b: {**b, "instruments": b["instruments"][:1]}, "geometry"),
        (lambda b: {**b, "output_sha256": "sha-y"}, "diverges"),
        (lambda b: {k: v for k, v in b.items() if k != "output_sha256"},
         "malformed"),
    ):
        path.write_text(json.dumps(mutate(good)), encoding="utf-8")
        with pytest.raises(PromotionError, match=pattern):
            _verify_fundamental_extension_baseline(config, fwd, "sha-x")


# --- schema 迁移：老 run 快照（无基本面四元组）------------------------------

def _old_style_run(tmp_path, tamper=False):
    """模拟 PR-4 之前的 miner 落盘：data 节没有基本面键，摘要按当时字段集。"""
    import dataclasses

    from src.factor_mining.miner import data_definition_sha256
    run_dir = tmp_path / "old-run"
    run_dir.mkdir(parents=True)
    data = DataConfig(mode="synthetic", synthetic_n_tickers=3)
    payload = {
        k: v for k, v in dataclasses.asdict(data).items()
        if not k.startswith("fundamental") and k != "financial_exclusions"
    }
    sha = data_definition_sha256(data, restrict_to=frozenset(payload))
    if tamper:
        payload["synthetic_n_tickers"] = 99
    (run_dir / "config.yaml").write_text(
        yaml.safe_dump({"run_id": "old", "data": payload,
                        "data_definition_sha256": sha}),
        encoding="utf-8")
    return run_dir


def test_a_pre_extension_snapshot_still_verifies(tmp_path):
    """老 run 不因 schema 扩展被误判"被编辑过" —— 缺键仅限四元组时按
    快照自身键集复现原规范化。"""
    data, sha = _load_run_data_config(_old_style_run(tmp_path))
    assert data.fundamental_store_root == ""
    assert data.synthetic_n_tickers == 3
    assert sha


def test_a_tampered_pre_extension_snapshot_is_still_refused(tmp_path):
    with pytest.raises(PromotionError, match="does not match the digest"):
        _load_run_data_config(_old_style_run(tmp_path, tamper=True))


# --- 部分四元组必须拒绝（codex #439 r1 P1）----------------------------------

def test_a_partial_fundamental_quartet_is_refused_at_mining(tmp_path):
    """只有 financial_exclusions 的配置曾溜过 store-root-only 判定：
    宇宙掩码照裁分母、面板却是纯量价 —— 另一个实验静默顶着存量配置跑。"""
    config = _config(tmp_path, tmp_path, tmp_path,
                     fundamental_store_root="",
                     fundamental_calendar_path="",
                     fundamental_fields=(),
                     financial_exclusions=("SZ000001",))
    with pytest.raises(ValueError, match="PARTIAL"):
        run_mining(config)
    with pytest.raises(ValueError, match="PARTIAL"):
        run_mining(config, fundamental_panel_factory=build_panel_factory())
    assert not (tmp_path / "mined").exists()


def test_a_partial_quartet_in_a_run_snapshot_is_refused_at_promotion(
        tmp_path):
    import dataclasses

    from src.factor_mining.miner import data_definition_sha256
    run_dir = tmp_path / "partial-run"
    run_dir.mkdir(parents=True)
    pool = FactorPool()
    pool.add(_pv_entry("cs_rank($volume)"))
    pool.save(run_dir)
    data = DataConfig(mode="synthetic",
                      financial_exclusions=("SZ000001",))
    (run_dir / "config.yaml").write_text(
        yaml.safe_dump({
            "run_id": "partial", "data": dataclasses.asdict(data),
            "data_definition_sha256": data_definition_sha256(data),
        }), encoding="utf-8")
    with pytest.raises(PromotionError, match="PARTIAL"):
        promote_run(_promotion_config(tmp_path, run_dir))
