"""Tests for the Phase 6 promotion CLI."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest
import yaml

from src.factor_mining.expression import parse_expression
from src.factor_mining.factor_pool import FactorPool, PoolEntry
from src.factor_mining.miner import DataConfig, data_definition_sha256
from src.factor_mining.promote import (
    PromotionConfig,
    PromotionError,
    _load_config,
    _load_run_data_config,
    promote_run,
)
from src.factor_mining.promote import (
    main as promote_main,
)
from src.factor_mining.validator import ValidationCriteria

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_RUN_DATA = {
    "mode": "synthetic",
    "synthetic_n_tickers": 8,
    "synthetic_n_dates": 120,
    "synthetic_seed": 7,
}


def _seed_run_dir(
    tmp_path: Path, n_factors: int = 3, *,
    data: dict | None = None, write_config: bool = True,
    extra_config: dict | None = None,
) -> Path:
    """Build a small Phase 3 run directory under ``tmp_path``.

    Mirrors the miner's on-disk contract: a factor pool plus the resolved
    ``config.yaml`` whose ``data:`` section promotion binds to.
    """
    run_dir = tmp_path / "runs" / "test-run"
    run_dir.mkdir(parents=True)
    pool = FactorPool()
    exprs = [
        parse_expression("cs_rank($volume)"),
        parse_expression("cs_rank($money)"),
        parse_expression("cs_zscore($volume)"),
    ][:n_factors]
    for i, expr in enumerate(exprs):
        pool.add(PoolEntry(
            expr=expr,
            fitness=float(2.0 - 0.2 * i),
            ic_mean=0.05, ic_std=0.10, ir=0.5,
            rank_ic_mean=0.04, rank_ic_std=0.08, rank_ir=0.5,
            turnover_daily=0.10, coverage=0.95, n_obs_per_day_min=20,
            expr_size=2, expr_hash=hash(expr),
        ))
    pool.save(run_dir)
    if write_config:
        payload = dict(data or _RUN_DATA)
        dump = {"run_id": "test-run", "data": payload}
        # Mirror the miner: record the digest AT MINING TIME so promote can
        # detect post-mining edits of the snapshot (codex P1 #415). A
        # deliberately malformed payload (schema-divergence tests) gets a
        # placeholder — the DataConfig parse refusal fires first anyway.
        try:
            dump["data_definition_sha256"] = data_definition_sha256(
                DataConfig(**payload))
        except TypeError:
            dump["data_definition_sha256"] = "unverifiable"
        if extra_config:
            dump.update(extra_config)
        (run_dir / "config.yaml").write_text(
            yaml.safe_dump(dump), encoding="utf-8",
        )
    return run_dir


def _criteria_loose() -> ValidationCriteria:
    """Permissive criteria — most synthetic factors pass."""
    return ValidationCriteria(
        is_oos_split_date="2024-04-01",
        min_oos_ir=0.0,
        min_oos_rank_ic_mean=0.0,
        max_pool_correlation=0.99,
        min_obs_per_segment=10,
    )


def _promotion_config(tmp_path: Path, run_dir: Path, version: str) -> PromotionConfig:
    # The data/hash pair must be the run's own snapshot: promote_run
    # re-loads and verifies it at the production-writing boundary.
    data, sha = _load_run_data_config(run_dir)
    return PromotionConfig(
        run_dir=run_dir,
        production_dir=tmp_path / "production",
        version=version,
        criteria=_criteria_loose(),
        data=data,
        data_definition_sha256=sha,
    )


# ---------------------------------------------------------------------------
# promote_run
# ---------------------------------------------------------------------------


def test_dry_run_writes_nothing(tmp_path):
    run_dir = _seed_run_dir(tmp_path)
    cfg = _promotion_config(tmp_path, run_dir, "v1")
    report = promote_run(cfg, dry_run=True)
    assert report.output_dir is None
    assert not (cfg.production_dir / "v1").exists()
    assert report.n_pool == 3


def test_full_run_writes_three_files(tmp_path):
    run_dir = _seed_run_dir(tmp_path)
    cfg = _promotion_config(tmp_path, run_dir, "v1")
    report = promote_run(cfg, dry_run=False)
    out = cfg.production_dir / "v1"
    assert report.output_dir == out
    assert (out / "factor_pool.parquet").is_file()
    assert (out / "factor_expressions.json").is_file()
    assert (out / "promotion_report.json").is_file()


def test_promotion_report_records_each_factor(tmp_path):
    run_dir = _seed_run_dir(tmp_path)
    cfg = _promotion_config(tmp_path, run_dir, "v1")
    promote_run(cfg, dry_run=False)
    rep = json.loads(
        (cfg.production_dir / "v1" / "promotion_report.json").read_text(encoding="utf-8")
    )
    assert rep["n_pool"] == 3
    assert "criteria" in rep
    assert len(rep["results"]) == 3
    for r in rep["results"]:
        assert "expr_str" in r
        assert "passes" in r
        assert "reasons" in r


def test_refuses_overwrite_existing_version(tmp_path):
    run_dir = _seed_run_dir(tmp_path)
    cfg = _promotion_config(tmp_path, run_dir, "v1")
    promote_run(cfg, dry_run=False)  # creates v1
    # Re-running with the same version label MUST raise
    with pytest.raises(PromotionError, match="already exists"):
        promote_run(cfg, dry_run=False)


def test_missing_run_dir_raises(tmp_path):
    cfg = PromotionConfig(
        run_dir=tmp_path / "does_not_exist",
        production_dir=tmp_path / "production",
        version="v1",
        criteria=_criteria_loose(),
        data=DataConfig(**_RUN_DATA),
        data_definition_sha256="test-sha",
    )
    with pytest.raises(PromotionError, match="does not exist"):
        promote_run(cfg, dry_run=True)


def test_promote_run_verifies_binding_at_the_boundary(tmp_path):
    # codex P1 #415: a programmatic caller that constructs PromotionConfig
    # directly must not be able to validate on a DIFFERENT panel while the
    # report claims data_source = run_dir/config.yaml. promote_run re-loads
    # the run snapshot and refuses a mismatch — data or hash.
    run_dir = _seed_run_dir(tmp_path)
    good_data, good_sha = _load_run_data_config(run_dir)
    tampered_data = DataConfig(**{**_RUN_DATA, "synthetic_seed": 12345})
    cfg = PromotionConfig(
        run_dir=run_dir, production_dir=tmp_path / "production",
        version="v1", criteria=_criteria_loose(),
        data=tampered_data, data_definition_sha256=good_sha,
    )
    with pytest.raises(PromotionError, match="does not match the run"):
        promote_run(cfg, dry_run=True)
    cfg2 = PromotionConfig(
        run_dir=run_dir, production_dir=tmp_path / "production",
        version="v1", criteria=_criteria_loose(),
        data=good_data, data_definition_sha256="forged-sha",
    )
    with pytest.raises(PromotionError, match="does not match the run"):
        promote_run(cfg2, dry_run=True)


def test_survivor_pool_has_only_passing_factors(tmp_path):
    run_dir = _seed_run_dir(tmp_path)
    cfg = _promotion_config(tmp_path, run_dir, "v1")
    report = promote_run(cfg, dry_run=False)
    # Load the saved production pool back and assert it contains only
    # the kept survivors.
    saved = FactorPool.load(cfg.production_dir / "v1")
    saved_hashes = {hash(e.expr) for e in saved.all_entries()}
    surviving_hashes = {r.expr_hash for r in report.results if r.passes}
    assert saved_hashes == surviving_hashes


# ---------------------------------------------------------------------------
# _load_config
# ---------------------------------------------------------------------------


def test_load_config_with_no_yaml_uses_defaults(tmp_path):
    run_dir = _seed_run_dir(tmp_path)
    cfg = _load_config(
        config_path=None,
        run_dir=run_dir,
        production_dir=tmp_path / "production",
        version="v1",
    )
    assert cfg.data.mode == "synthetic"
    assert cfg.criteria.min_oos_ir == 0.3  # D4 default


def test_load_config_reads_yaml_criteria(tmp_path):
    run_dir = _seed_run_dir(tmp_path)
    config_path = tmp_path / "promote.yaml"
    config_path.write_text(
        "criteria:\n"
        "  is_oos_split_date: '2024-06-15'\n"
        "  min_oos_ir: 0.5\n"
        "  min_obs_per_segment: 15\n",
        encoding="utf-8",
    )
    cfg = _load_config(
        config_path=config_path,
        run_dir=run_dir,
        production_dir=tmp_path / "production",
        version="v1",
    )
    assert cfg.criteria.min_oos_ir == 0.5
    assert cfg.criteria.is_oos_split_date == "2024-06-15"


# ---------------------------------------------------------------------------
# Data-definition binding (external finding, 2026-08-10): promotion must
# re-validate on EXACTLY the data definition the run was mined with.
# ---------------------------------------------------------------------------


def test_data_comes_from_run_config_not_defaults(tmp_path):
    # The run's config.yaml is authoritative — its knobs (not any default)
    # must land in the parsed DataConfig.
    run_dir = _seed_run_dir(
        tmp_path,
        data={**_RUN_DATA, "synthetic_n_dates": 77, "synthetic_seed": 99},
    )
    cfg = _load_config(None, run_dir, tmp_path / "production", "v1")
    assert cfg.data.synthetic_n_dates == 77
    assert cfg.data.synthetic_seed == 99
    assert cfg.data_definition_sha256


def test_operator_config_with_data_section_is_refused(tmp_path):
    run_dir = _seed_run_dir(tmp_path)
    config_path = tmp_path / "promote.yaml"
    config_path.write_text(
        "criteria:\n  is_oos_split_date: '2024-06-15'\n"
        "data:\n  mode: synthetic\n",
        encoding="utf-8",
    )
    with pytest.raises(PromotionError, match="bound to the mined run"):
        _load_config(config_path, run_dir, tmp_path / "production", "v1")


def test_run_dir_without_config_yaml_is_refused(tmp_path):
    run_dir = _seed_run_dir(tmp_path, write_config=False)
    with pytest.raises(PromotionError, match="no resolved config.yaml"):
        _load_config(None, run_dir, tmp_path / "production", "v1")


def test_campaign_shaped_data_section_parses(tmp_path):
    # The exact regression: a pv_incremental_v1-shaped data section (fields
    # + forward_return_price) used to raise TypeError against the promotion
    # mirror dataclass. It must round-trip into the miner's own DataConfig.
    campaign_data = {
        "mode": "pit",
        "pit_provider_uri": "D:/qlib_data/my_cn_data_pit",
        "delisted_registry_path": "D:/data/delisted.parquet",
        "universe_name": "csi300",
        "start_date": "2019-01-01",
        "end_date": "2024-12-31",
        "forward_horizon": 1,
        "fields": ["open", "close", "high", "low", "volume", "money", "vwap"],
        "forward_return_price": "close",
    }
    run_dir = _seed_run_dir(tmp_path, data=campaign_data)
    data, sha = _load_run_data_config(run_dir)
    assert data.mode == "pit"
    assert data.forward_return_price == "close"
    assert tuple(data.fields) == tuple(campaign_data["fields"])
    assert len(sha) == 64


_PIT_RUN_DATA = {
    "mode": "pit", "pit_provider_uri": "x", "delisted_registry_path": "y",
    "start_date": "2019-01-01", "end_date": "2022-12-31",
}


def _write_promote_yaml(tmp_path: Path, body: str) -> Path:
    config_path = tmp_path / "promote.yaml"
    config_path.write_text(body, encoding="utf-8")
    return config_path


def test_pit_mode_requires_explicit_oos_split(tmp_path):
    # Deriving an OOS boundary from synthetic defaults would silently
    # adjudicate a real-data run on an arbitrary date — refused.
    run_dir = _seed_run_dir(tmp_path, data=_PIT_RUN_DATA)
    with pytest.raises(PromotionError, match="is_oos_split_date"):
        _load_config(None, run_dir, tmp_path / "production", "v1")


def test_pit_mode_requires_governed_validation_window(tmp_path):
    # codex P1 #415 (window): the mining panel deliberately ends at the IS
    # cutoff, so a split alone cannot produce genuine OOS data — the
    # governed extension is mandatory for PIT promotion.
    run_dir = _seed_run_dir(tmp_path, data=_PIT_RUN_DATA)
    cfg_path = _write_promote_yaml(
        tmp_path, "criteria:\n  is_oos_split_date: '2023-06-30'\n")
    with pytest.raises(PromotionError, match="validation.end_date"):
        _load_config(cfg_path, run_dir, tmp_path / "production", "v1")


def _seed_pit_inputs(tmp_path: Path) -> dict:
    """Fake-but-fingerprintable PIT inputs + a data dict pointing at them."""
    bundle = tmp_path / "bundle"
    for rel, content in (
        ("calendars/day.txt", b"2019-01-02\n2019-01-03\n"),
        ("instruments/all.txt", b"SH600000\t2019-01-02\t2019-01-03\n"),
        ("features/sh600000/close.day.bin", b"\x01\x02\x03"),
    ):
        path = bundle / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    registry = tmp_path / "registry.parquet"
    registry.write_bytes(b"registry-bytes-v1")
    return {**_PIT_RUN_DATA,
            "pit_provider_uri": str(bundle),
            "delisted_registry_path": str(registry)}


def test_pit_governed_window_extends_the_panel(tmp_path):
    from src.factor_mining.miner import pit_binding_fingerprints

    pit_data = _seed_pit_inputs(tmp_path)
    fingerprints = pit_binding_fingerprints(DataConfig(**pit_data))
    run_dir = _seed_run_dir(tmp_path, data=pit_data,
                            extra_config=fingerprints)
    cfg_path = _write_promote_yaml(
        tmp_path,
        "criteria:\n  is_oos_split_date: '2023-06-30'\n"
        "validation:\n  end_date: '2024-12-31'\n",
    )
    cfg = _load_config(cfg_path, run_dir, tmp_path / "production", "v1")
    # The effective panel extends; everything else stays the mined snapshot.
    assert cfg.data.end_date == "2024-12-31"
    assert cfg.validation_end_date == "2024-12-31"
    assert cfg.data.start_date == _PIT_RUN_DATA["start_date"]
    # ...and the boundary checks (window, fingerprints) all accept:
    # promote_run gets PAST them and fails only deeper, at real panel
    # build against the fake bundle.
    with pytest.raises(Exception) as excinfo:
        promote_run(cfg, dry_run=True)
    msg = str(excinfo.value)
    assert "does not match" not in msg
    assert "fingerprint" not in msg


def test_pit_split_on_the_cutoff_day_is_refused(tmp_path):
    # codex P1 #415 r4: the validator grades OOS as date >= split, so a
    # split ON the mined end_date grades that GP-visible day as OOS.
    run_dir = _seed_run_dir(tmp_path, data=_PIT_RUN_DATA)
    cfg_path = _write_promote_yaml(
        tmp_path,
        "criteria:\n  is_oos_split_date: '2022-12-31'\n"
        "validation:\n  end_date: '2024-12-31'\n",
    )
    with pytest.raises(PromotionError, match="GP-visible"):
        _load_config(cfg_path, run_dir, tmp_path / "production", "v1")


def test_pit_window_dates_are_parsed_not_string_compared(tmp_path):
    # codex P2 #415 r4: '2022-9-30' orders lexically AFTER '2022-12-31';
    # parsed comparison must refuse it as inside the GP-visible period.
    run_dir = _seed_run_dir(tmp_path, data=_PIT_RUN_DATA)
    cfg_path = _write_promote_yaml(
        tmp_path,
        "criteria:\n  is_oos_split_date: '2022-9-30'\n"
        "validation:\n  end_date: '2024-12-31'\n",
    )
    with pytest.raises(PromotionError, match="GP-visible"):
        _load_config(cfg_path, run_dir, tmp_path / "production", "v1")
    # ...and garbage fails loud as unparseable, not as mis-ordered.
    cfg_path = _write_promote_yaml(
        tmp_path,
        "criteria:\n  is_oos_split_date: 'not-a-date'\n"
        "validation:\n  end_date: '2024-12-31'\n",
    )
    with pytest.raises(PromotionError, match="not a parseable date"):
        _load_config(cfg_path, run_dir, tmp_path / "production", "v1")


def test_refreshed_pit_inputs_are_detected(tmp_path):
    # codex P1 #415 r4: an in-place refresh of the bundle/registry between
    # mining and promotion changes the panel bytes while every config
    # value (and thus the data-definition digest) stays identical — the
    # recorded CONTENT fingerprints catch it.
    from src.factor_mining.miner import pit_binding_fingerprints

    pit_data = _seed_pit_inputs(tmp_path)
    fingerprints = pit_binding_fingerprints(DataConfig(**pit_data))
    run_dir = _seed_run_dir(tmp_path, data=pit_data,
                            extra_config=fingerprints)
    cfg_path = _write_promote_yaml(
        tmp_path,
        "criteria:\n  is_oos_split_date: '2023-06-30'\n"
        "validation:\n  end_date: '2024-12-31'\n",
    )
    cfg = _load_config(cfg_path, run_dir, tmp_path / "production", "v1")
    # Refresh the registry in place — same path, different bytes.
    Path(pit_data["delisted_registry_path"]).write_bytes(b"registry-v2")
    with pytest.raises(PromotionError, match="refreshed in place"):
        promote_run(cfg, dry_run=True)


def test_pit_run_without_fingerprints_is_refused(tmp_path):
    pit_data = _seed_pit_inputs(tmp_path)
    run_dir = _seed_run_dir(tmp_path, data=pit_data)  # no fingerprints
    cfg_path = _write_promote_yaml(
        tmp_path,
        "criteria:\n  is_oos_split_date: '2023-06-30'\n"
        "validation:\n  end_date: '2024-12-31'\n",
    )
    cfg = _load_config(cfg_path, run_dir, tmp_path / "production", "v1")
    with pytest.raises(PromotionError, match="no PIT content fingerprints"):
        promote_run(cfg, dry_run=True)


def test_unquoted_yaml_dates_are_normalized_everywhere(tmp_path):
    # codex P1 #415 r6: unquoted YAML dates parse into datetime.date. The
    # criteria/validation sections and the run snapshot's data section
    # must all normalize to ISO strings, or digest recomputation and
    # dataclass equality silently disagree with the miner's own strings.
    pit_data = _seed_pit_inputs(tmp_path)
    run_dir = _seed_run_dir(tmp_path, data=pit_data)
    cfg_path = _write_promote_yaml(
        tmp_path,
        "criteria:\n  is_oos_split_date: 2023-06-30\n"
        "validation:\n  end_date: 2024-12-31\n",  # unquoted on purpose
    )
    cfg = _load_config(cfg_path, run_dir, tmp_path / "production", "v1")
    assert cfg.validation_end_date == "2024-12-31"
    assert isinstance(cfg.validation_end_date, str)
    assert cfg.data.end_date == "2024-12-31"
    assert isinstance(cfg.data.end_date, str)


def test_malformed_run_snapshot_is_a_controlled_refusal(tmp_path):
    # codex P2 #415 r5: a truncated config.yaml raised yaml.YAMLError and
    # a top-level list raised AttributeError — tracebacks, not the
    # PromotionError refusal the CLI promises.
    run_dir = _seed_run_dir(tmp_path)
    cfg_path = _write_promote_yaml(tmp_path, "criteria: {}\n")
    (run_dir / "config.yaml").write_text(
        "data: [unclosed", encoding="utf-8")
    with pytest.raises(PromotionError, match="not valid YAML"):
        _load_config(cfg_path, run_dir, tmp_path / "production", "v1")
    (run_dir / "config.yaml").write_text(
        "- item1\n- item2\n", encoding="utf-8")
    with pytest.raises(PromotionError, match="must be a YAML mapping"):
        _load_config(cfg_path, run_dir, tmp_path / "production", "v1")


def test_malformed_promotion_config_is_a_controlled_refusal(tmp_path):
    run_dir = _seed_run_dir(tmp_path)
    cfg_path = tmp_path / "promote.yaml"
    cfg_path.write_text("- not\n- a\n- mapping\n", encoding="utf-8")
    with pytest.raises(PromotionError, match="must be a YAML mapping"):
        _load_config(cfg_path, run_dir, tmp_path / "production", "v1")


def test_falsy_non_mapping_documents_are_refused(tmp_path):
    # codex P2 #415 r8: `or {}` laundered [], false, 0 into "no overrides"
    # — a synthetic run would promote on DEFAULT criteria instead of
    # refusing the malformed document. Only YAML null means empty.
    run_dir = _seed_run_dir(tmp_path)
    for body in ("[]\n", "false\n", "0\n"):
        cfg_path = tmp_path / "promote.yaml"
        cfg_path.write_text(body, encoding="utf-8")
        with pytest.raises(PromotionError, match="must be a YAML mapping"):
            _load_config(cfg_path, run_dir, tmp_path / "production", "v1")
    # ...while a genuinely empty document still means "defaults".
    (tmp_path / "promote.yaml").write_text("", encoding="utf-8")
    cfg = _load_config(
        tmp_path / "promote.yaml", run_dir, tmp_path / "production", "v1")
    assert cfg.validation_end_date is None


def test_timezone_bearing_dates_are_refused(tmp_path):
    # codex P2 #415 r8+r9: normalizing the tz only fixed the window
    # comparison — the aware original stayed in the effective config and
    # validate_pool's reparse would TypeError against the naive panel
    # index. Wall-clock semantics: tz-bearing values are refused loud,
    # wherever they appear.
    run_dir = _seed_run_dir(tmp_path, data=_PIT_RUN_DATA)
    for body in (
        "criteria:\n  is_oos_split_date: '2023-06-30'\n"
        "validation:\n  end_date: '2024-12-31T00:00:00Z'\n",
        "criteria:\n  is_oos_split_date: '2023-06-30T00:00:00+08:00'\n"
        "validation:\n  end_date: '2024-12-31'\n",
    ):
        cfg_path = _write_promote_yaml(tmp_path, body)
        with pytest.raises(PromotionError, match="timezone-bearing"):
            _load_config(cfg_path, run_dir, tmp_path / "production", "v1")
    # ...while naive datetime spellings still parse as their wall date.
    cfg_path = _write_promote_yaml(
        tmp_path,
        "criteria:\n  is_oos_split_date: '2023-06-30'\n"
        "validation:\n  end_date: '2024-12-31 00:00:00'\n",
    )
    cfg = _load_config(cfg_path, run_dir, tmp_path / "production", "v1")
    assert cfg.validation_end_date == "2024-12-31 00:00:00"


def test_pit_embargo_clears_label_lookahead(tmp_path):
    # codex P1 #415 r11: forward_return labels T with prices at
    # T+1..T+H+1 TRADING days, so the last mined labels consume prices
    # past the calendar cutoff — a split merely after end_date can still
    # grade GP-consumed prices as OOS.
    from src.factor_mining.promote import _check_pit_embargo

    # Trading days mirroring the real 2022→2023 turn: 12-29, 12-30 are
    # the last mined days; 01-03, 01-04, 01-05, 01-06 follow.
    idx = pd.DatetimeIndex([
        "2022-12-29", "2022-12-30", "2023-01-03", "2023-01-04",
        "2023-01-05", "2023-01-06", "2023-01-09",
    ])
    # H=1: the 12-30 label consumes 01-03 and 01-04 → first clean 01-05.
    with pytest.raises(PromotionError, match="label-lookahead embargo"):
        _check_pit_embargo(idx, "2022-12-31", "2023-01-03", 1)
    with pytest.raises(PromotionError, match="Move the split to 2023-01-05"):
        _check_pit_embargo(idx, "2022-12-31", "2023-01-04", 1)
    _check_pit_embargo(idx, "2022-12-31", "2023-01-05", 1)  # clean
    # H=2 widens the embargo by one trading day.
    with pytest.raises(PromotionError, match="embargo"):
        _check_pit_embargo(idx, "2022-12-31", "2023-01-05", 2)
    _check_pit_embargo(idx, "2022-12-31", "2023-01-06", 2)
    # An extension too short to clear the embargo at all is refused.
    with pytest.raises(PromotionError, match="no trading day beyond"):
        _check_pit_embargo(idx[:4], "2022-12-31", "2023-01-04", 1)


def test_promote_run_enforces_the_embargo_on_the_panel(tmp_path, monkeypatch):
    # The boundary actually calls the embargo check on the BUILT panel's
    # index — proven by stubbing the panel builder with a real trading
    # calendar and asserting the refusal fires from promote_run.
    import src.factor_mining.promote as promote_mod
    from src.factor_mining.miner import pit_binding_fingerprints

    pit_data = _seed_pit_inputs(tmp_path)
    fingerprints = pit_binding_fingerprints(DataConfig(**pit_data))
    run_dir = _seed_run_dir(tmp_path, data=pit_data,
                            extra_config=fingerprints)
    cfg_path = _write_promote_yaml(
        tmp_path,
        "criteria:\n  is_oos_split_date: '2023-01-03'\n"
        "validation:\n  end_date: '2024-12-31'\n",
    )
    cfg = _load_config(cfg_path, run_dir, tmp_path / "production", "v1")
    idx = pd.bdate_range("2022-12-01", "2023-03-31")
    frame = pd.DataFrame(0.0, index=idx, columns=["SH600000"])
    monkeypatch.setattr(promote_mod, "build_panel_for_data",
                        lambda data: ({"$close": frame}, frame))
    with pytest.raises(PromotionError, match="label-lookahead embargo"):
        promote_run(cfg, dry_run=True)


def test_falsy_date_overrides_are_refused_not_defaulted(tmp_path):
    # codex P2 #415 r10: `validation: {end_date: false}` (or 0, [], {})
    # failed the truthiness check and was treated as if the key were
    # ABSENT — a malformed operator override silently fell back to the
    # default behavior instead of refusing.
    run_dir = _seed_run_dir(tmp_path)
    for body in ("validation:\n  end_date: false\n",
                 "validation:\n  end_date: 0\n",
                 "validation:\n  end_date: []\n",
                 "validation:\n  end_date: {}\n",
                 "criteria:\n  is_oos_split_date: false\n"):
        cfg_path = _write_promote_yaml(tmp_path, body)
        with pytest.raises(PromotionError, match="not a usable date"):
            _load_config(cfg_path, run_dir, tmp_path / "production", "v1")
    # ...while explicit null keeps meaning "as if absent": the synthetic
    # auto-split still applies and no extension is recorded.
    cfg_path = _write_promote_yaml(
        tmp_path,
        "validation:\n  end_date: null\n"
        "criteria:\n  is_oos_split_date: null\n",
    )
    cfg = _load_config(cfg_path, run_dir, tmp_path / "production", "v1")
    assert cfg.validation_end_date is None
    assert cfg.criteria.is_oos_split_date is not None  # auto 80/20 split


def test_scalar_config_sections_are_a_controlled_refusal(tmp_path):
    # codex P2 #415 r7: `validation: typo` / `criteria: 42` made dict()
    # raise ValueError/TypeError past the CLI's PromotionError branch.
    run_dir = _seed_run_dir(tmp_path)
    for body in ("validation: typo\n", "criteria: 42\n",
                 "validation:\n- a\n- b\n"):
        cfg_path = _write_promote_yaml(tmp_path, body)
        with pytest.raises(PromotionError, match="must be a YAML"):
            _load_config(cfg_path, run_dir, tmp_path / "production", "v1")


def test_boundary_refuses_synthetic_extension(tmp_path):
    # codex P2 #415 r4: _load_config refuses this for the CLI; the
    # production-writing boundary must refuse the programmatic version.
    run_dir = _seed_run_dir(tmp_path)
    data, sha = _load_run_data_config(run_dir)
    cfg = PromotionConfig(
        run_dir=run_dir, production_dir=tmp_path / "production",
        version="v1", criteria=_criteria_loose(),
        data=replace(data, end_date="2099-12-31"),
        data_definition_sha256=sha,
        validation_end_date="2099-12-31",
    )
    with pytest.raises(PromotionError, match="PIT-mode runs only"):
        promote_run(cfg, dry_run=True)


def test_pit_split_must_lie_inside_the_extension(tmp_path):
    run_dir = _seed_run_dir(tmp_path, data=_PIT_RUN_DATA)
    # split BEFORE the mining cutoff -> would grade GP-visible data as OOS
    cfg_path = _write_promote_yaml(
        tmp_path,
        "criteria:\n  is_oos_split_date: '2021-06-30'\n"
        "validation:\n  end_date: '2024-12-31'\n",
    )
    with pytest.raises(PromotionError, match="GP-visible"):
        _load_config(cfg_path, run_dir, tmp_path / "production", "v1")
    # split AT/BEYOND the extension -> zero OOS observations
    cfg_path = _write_promote_yaml(
        tmp_path,
        "criteria:\n  is_oos_split_date: '2024-12-31'\n"
        "validation:\n  end_date: '2024-12-31'\n",
    )
    with pytest.raises(PromotionError, match="no OOS observations"):
        _load_config(cfg_path, run_dir, tmp_path / "production", "v1")


def test_pit_extension_must_be_beyond_the_mined_end(tmp_path):
    run_dir = _seed_run_dir(tmp_path, data=_PIT_RUN_DATA)
    cfg_path = _write_promote_yaml(
        tmp_path,
        "criteria:\n  is_oos_split_date: '2022-06-30'\n"
        "validation:\n  end_date: '2022-12-31'\n",  # == mined end
    )
    with pytest.raises(PromotionError, match="strictly AFTER"):
        _load_config(cfg_path, run_dir, tmp_path / "production", "v1")


def test_synthetic_mode_refuses_validation_window(tmp_path):
    # The synthetic panel ignores calendar dates — an "extension" there
    # would be a no-op pretending to be governance.
    run_dir = _seed_run_dir(tmp_path)
    cfg_path = _write_promote_yaml(
        tmp_path, "validation:\n  end_date: '2024-12-31'\n")
    with pytest.raises(PromotionError, match="PIT-mode runs only"):
        _load_config(cfg_path, run_dir, tmp_path / "production", "v1")


def test_edited_snapshot_is_detected_by_recorded_digest(tmp_path):
    # codex P1 #415 (digest): editing run_dir/config.yaml after mining must
    # be caught — the recorded mining-time digest no longer matches.
    run_dir = _seed_run_dir(tmp_path)
    config_path = run_dir / "config.yaml"
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["data"]["synthetic_seed"] = 4242  # the post-mining hand edit
    config_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(PromotionError, match="edited after mining"):
        _load_run_data_config(run_dir)


def test_run_without_recorded_digest_is_refused(tmp_path):
    run_dir = _seed_run_dir(tmp_path)
    config_path = run_dir / "config.yaml"
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    del raw["data_definition_sha256"]
    config_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(PromotionError, match="no data_definition_sha256"):
        _load_run_data_config(run_dir)


def test_malformed_run_data_section_is_refused(tmp_path):
    run_dir = _seed_run_dir(
        tmp_path, data={**_RUN_DATA, "no_such_knob": 1},
    )
    with pytest.raises(PromotionError, match="does not parse"):
        _load_run_data_config(run_dir)


def test_promotion_report_records_data_definition(tmp_path):
    run_dir = _seed_run_dir(tmp_path)
    cfg = _load_config(None, run_dir, tmp_path / "production", "v1")
    # Loosen criteria so the synthetic factors survive to a written report.
    cfg = PromotionConfig(
        run_dir=cfg.run_dir, production_dir=cfg.production_dir,
        version=cfg.version, criteria=_criteria_loose(),
        data=cfg.data, data_definition_sha256=cfg.data_definition_sha256,
    )
    promote_run(cfg, dry_run=False)
    rep = json.loads(
        (cfg.production_dir / "v1" / "promotion_report.json")
        .read_text(encoding="utf-8")
    )
    assert rep["data"]["synthetic_n_dates"] == _RUN_DATA["synthetic_n_dates"]
    # Each digest verifies over the dict RIGHT BESIDE it (codex P2 #415 r2)
    # via the shared canonicalization — downstream recomputation must agree.
    assert rep["mined_data_sha256"] == cfg.data_definition_sha256
    assert rep["mined_data_sha256"] == data_definition_sha256(
        DataConfig(**rep["mined_data"]))
    assert rep["data_sha256"] == data_definition_sha256(
        DataConfig(**rep["data"]))
    assert rep["mined_data_source"] == "run_dir/config.yaml"
    assert rep["validation_end_date"] is None
    # No extension on the synthetic path: the two pairs coincide.
    assert rep["data"] == rep["mined_data"]


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------


def test_cli_dry_run_exits_zero(tmp_path):
    run_dir = _seed_run_dir(tmp_path)
    # Use a tiny YAML to control output paths in tmp_path
    config_yaml = tmp_path / "p.yaml"
    config_yaml.write_text(
        "criteria:\n"
        "  is_oos_split_date: '2024-04-01'\n"
        "  min_oos_ir: 0.0\n"
        "  min_oos_rank_ic_mean: 0.0\n"
        "  max_pool_correlation: 0.99\n"
        "  min_obs_per_segment: 10\n",
        encoding="utf-8",
    )
    rc = promote_main(
        [
            "--run", str(run_dir),
            "--to", "v1",
            "--production-dir", str(tmp_path / "production"),
            "--config", str(config_yaml),
            "--dry-run",
        ]
    )
    assert rc == 0
    # Nothing written
    assert not (tmp_path / "production" / "v1").exists()


def test_cli_full_run_writes_files(tmp_path):
    run_dir = _seed_run_dir(tmp_path)
    config_yaml = tmp_path / "p.yaml"
    config_yaml.write_text(
        "criteria:\n"
        "  is_oos_split_date: '2024-04-01'\n"
        "  min_oos_ir: 0.0\n"
        "  min_oos_rank_ic_mean: 0.0\n"
        "  max_pool_correlation: 0.99\n"
        "  min_obs_per_segment: 10\n",
        encoding="utf-8",
    )
    rc = promote_main(
        [
            "--run", str(run_dir),
            "--to", "v1",
            "--production-dir", str(tmp_path / "production"),
            "--config", str(config_yaml),
        ]
    )
    assert rc == 0
    out = tmp_path / "production" / "v1"
    assert (out / "factor_pool.parquet").is_file()
    assert (out / "factor_expressions.json").is_file()
    assert (out / "promotion_report.json").is_file()


def test_cli_missing_run_dir_exits_nonzero(tmp_path):
    rc = promote_main(
        [
            "--run", str(tmp_path / "nope"),
            "--to", "v1",
            "--production-dir", str(tmp_path / "production"),
        ]
    )
    assert rc != 0


def test_cli_subprocess_smoke(tmp_path):
    """End-to-end CLI invocation via subprocess."""
    run_dir = _seed_run_dir(tmp_path)
    config_yaml = tmp_path / "p.yaml"
    config_yaml.write_text(
        "criteria:\n"
        "  is_oos_split_date: '2024-04-01'\n"
        "  min_oos_ir: 0.0\n"
        "  min_oos_rank_ic_mean: 0.0\n"
        "  max_pool_correlation: 0.99\n"
        "  min_obs_per_segment: 10\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable, "-m", "src.factor_mining.promote",
            "--run", str(run_dir),
            "--to", "v1",
            "--production-dir", str(tmp_path / "production"),
            "--config", str(config_yaml),
            "--dry-run",
        ],
        capture_output=True, text=True,
        cwd=str(Path(__file__).resolve().parents[3]),
        timeout=60,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "dry-run" in result.stdout.lower()


# ---------------------------------------------------------------------------
# D5 strict gate
# ---------------------------------------------------------------------------


def test_promote_does_not_import_qlib_or_pit():
    import inspect

    import src.factor_mining.promote as mod

    src = inspect.getsource(mod)
    # No top-level qlib import (lazy inside PIT branch only)
    for line in src.splitlines():
        s = line.lstrip()
        if line == s and (s.startswith("from qlib") or s.startswith("import qlib")):
            pytest.fail(f"Top-level qlib import in promote.py: {line!r}")
    # promote.py is allowed to lazy-import src.pit.query inside the PIT
    # branch (mirrors the miner pattern); verify it's NOT at top level.
    for line in src.splitlines():
        s = line.lstrip()
        if line == s and (
            s.startswith("from src.pit") or s.startswith("import src.pit")
        ):
            pytest.fail(f"Top-level src.pit import in promote.py: {line!r}")


def test_promote_run_enforces_pit_window_for_programmatic_callers(tmp_path):
    # codex P1 #415 r2: constructing PromotionConfig directly with a PIT
    # run and NO extension (or a split outside it) must be refused at the
    # production-writing boundary, not only in _load_config.
    run_dir = _seed_run_dir(tmp_path, data=_PIT_RUN_DATA)
    data, sha = _load_run_data_config(run_dir)
    no_extension = PromotionConfig(
        run_dir=run_dir, production_dir=tmp_path / "production",
        version="v1",
        criteria=ValidationCriteria(is_oos_split_date="2023-06-30"),
        data=data, data_definition_sha256=sha,
        validation_end_date=None,
    )
    with pytest.raises(PromotionError, match="validation.end_date"):
        promote_run(no_extension, dry_run=True)
    bad_split = PromotionConfig(
        run_dir=run_dir, production_dir=tmp_path / "production",
        version="v1",
        criteria=ValidationCriteria(is_oos_split_date="2021-06-30"),
        data=replace(data, end_date="2024-12-31"),
        data_definition_sha256=sha,
        validation_end_date="2024-12-31",
    )
    with pytest.raises(PromotionError, match="GP-visible"):
        promote_run(bad_split, dry_run=True)


def test_dry_run_also_invokes_the_fundamental_refusal(tmp_path, monkeypatch):
    """dry-run 是操作员的预览：政策检查两种模式都要跑（codex #437 r5 P2）。

    若拒绝只在真跑时触发，预览会对一个**必然失败**的池说"would be kept" ——
    对生产资格撒谎。只有文件系统写入才允许按 dry_run 条件化。

    合成面板没有财报列（基本面因子在验证层就会 fail-loud，到不了写盘），
    所以这里钉的是**缝**：dry-run 也必须**调用**拒绝，并把幸存者池递给它 ——
    与「拒绝对基本面池抛 PromotionError」的既有回归组合，即覆盖该行为。
    真实基本面 run 的端到端在注入缝 PR 里补。
    """
    import src.factor_mining.promote as promote_mod

    calls: list = []
    real = promote_mod._refuse_fundamental_pool_in_production

    def spy(survivor_pool, target_dir):
        calls.append((len(list(survivor_pool.all_entries())), target_dir))
        return real(survivor_pool, target_dir)

    monkeypatch.setattr(
        promote_mod, "_refuse_fundamental_pool_in_production", spy)
    run_dir = _seed_run_dir(tmp_path)
    cfg = _promotion_config(tmp_path, run_dir, "v1")
    report = promote_run(cfg, dry_run=True)
    assert calls, "dry-run 没有调用生产边界拒绝 —— 预览与真跑政策不一致"
    assert report.output_dir is None
    assert not (cfg.production_dir / "v1").exists()


def test_a_validation_refusal_becomes_a_controlled_promotion_failure(
        tmp_path, monkeypatch):
    """validator 的拒绝必须在晋升边界翻译成 PromotionError：CLI 的契约是
    受控的 "Promotion failed: ..." + 非零返回码，不是裸 traceback
    （codex #448 r4 P2）。"""
    import src.factor_mining.promote as promote_mod
    from src.factor_mining.validator import ValidationError

    run_dir = _seed_run_dir(tmp_path)
    config = _promotion_config(tmp_path, run_dir, "v1")

    def refusing(*_a, **_k):
        raise ValidationError("survivor pool cannot be constructed")

    monkeypatch.setattr(promote_mod, "filter_correlated", refusing)
    with pytest.raises(PromotionError, match="validation refused"):
        promote_mod.promote_run(config)
    assert not (tmp_path / "production" / "v1").exists()
