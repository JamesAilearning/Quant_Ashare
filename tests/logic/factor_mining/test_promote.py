"""Tests for the Phase 6 promotion CLI."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from src.factor_mining.expression import parse_expression
from src.factor_mining.factor_pool import FactorPool, PoolEntry
from src.factor_mining.promote import (
    PromotionConfig,
    PromotionDataConfig,
    PromotionError,
    _load_config,
    promote_run,
)
from src.factor_mining.promote import (
    main as promote_main,
)
from src.factor_mining.validator import ValidationCriteria

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _seed_run_dir(tmp_path: Path, n_factors: int = 3) -> Path:
    """Build a small Phase 3 run directory under ``tmp_path``."""
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
    return PromotionConfig(
        run_dir=run_dir,
        production_dir=tmp_path / "production",
        version=version,
        criteria=_criteria_loose(),
        data=PromotionDataConfig(
            mode="synthetic",
            synthetic_n_tickers=8,
            synthetic_n_dates=120,
            synthetic_seed=7,
        ),
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
        data=PromotionDataConfig(),
    )
    with pytest.raises(PromotionError, match="does not exist"):
        promote_run(cfg, dry_run=True)


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
        "  min_obs_per_segment: 15\n"
        "data:\n"
        "  mode: synthetic\n"
        "  synthetic_n_dates: 200\n",
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
    assert cfg.data.synthetic_n_dates == 200


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
        "  min_obs_per_segment: 10\n"
        "data:\n"
        "  mode: synthetic\n"
        "  synthetic_n_dates: 120\n"
        "  synthetic_n_tickers: 8\n"
        "  synthetic_seed: 7\n",
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
        "  min_obs_per_segment: 10\n"
        "data:\n"
        "  mode: synthetic\n"
        "  synthetic_n_dates: 120\n"
        "  synthetic_n_tickers: 8\n"
        "  synthetic_seed: 7\n",
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
        "  min_obs_per_segment: 10\n"
        "data:\n"
        "  mode: synthetic\n"
        "  synthetic_n_dates: 120\n"
        "  synthetic_n_tickers: 8\n"
        "  synthetic_seed: 7\n",
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
