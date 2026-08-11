"""Tests for the Phase 6 promotion CLI."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

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


def test_pit_governed_window_extends_the_panel(tmp_path):
    run_dir = _seed_run_dir(tmp_path, data=_PIT_RUN_DATA)
    cfg_path = _write_promote_yaml(
        tmp_path,
        "criteria:\n  is_oos_split_date: '2022-12-31'\n"
        "validation:\n  end_date: '2024-12-31'\n",
    )
    cfg = _load_config(cfg_path, run_dir, tmp_path / "production", "v1")
    # The effective panel extends; everything else stays the mined snapshot.
    assert cfg.data.end_date == "2024-12-31"
    assert cfg.validation_end_date == "2024-12-31"
    assert cfg.data.start_date == _PIT_RUN_DATA["start_date"]
    # ...and the boundary check accepts the extension: promote_run gets
    # PAST the binding verification and fails only deeper, at panel build
    # (which needs a real PIT bundle this test does not have).
    with pytest.raises(Exception) as excinfo:
        promote_run(cfg, dry_run=True)
    assert "does not match" not in str(excinfo.value)
    assert "delisted registry" in str(excinfo.value)


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
    assert rep["data_definition_sha256"] == cfg.data_definition_sha256
    assert rep["data_source"] == "run_dir/config.yaml"


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
