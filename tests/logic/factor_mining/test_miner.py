"""Tests for the miner CLI orchestrator."""

from __future__ import annotations

import inspect
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from src.factor_mining.miner import (
    DataConfig,
    MinerConfig,
    build_panel,
    load_config,
    run_mining,
)


def _smoke_config(tmp_path) -> Path:
    """Build a fast smoke config under tmp_path."""
    config = {
        "run_id": "test-run",
        "output_dir": str(tmp_path / "mined"),
        "data": {
            "mode": "synthetic",
            "synthetic_n_tickers": 8,
            "synthetic_n_dates": 30,
            "synthetic_seed": 1234,
        },
        "gp": {
            "population_size": 6,
            "n_generations": 2,
            "tournament_size": 3,
            "elite_frac": 0.05,
            "p_crossover": 0.7,
            "p_mutate_subtree": 0.15,
            "p_mutate_point": 0.10,
            "p_mutate_const": 0.05,
            "max_depth": 3,
            "min_depth": 2,
            "target_kind": "CSF",
            "target_taint": "PURE",
            "seed": 42,
        },
        "fitness": {
            "w_ic": 1.0,
            "w_ir": 0.5,
            "w_rankic": 0.5,
            "w_turnover": 0.2,
            "w_corr": 0.8,
            "w_complexity": 0.01,
            "cost_rate": 0.003,
            "coverage_min": 0.8,
            "variance_days_frac_min": 0.7,
            "variance_min": 1.0e-6,
            "extreme_outlier_frac_max": 0.05,
            "extreme_outlier_magnitude": 1.0e8,
        },
    }
    config_path = tmp_path / "smoke.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return config_path


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def test_load_config_parses_smoke_yaml(tmp_path):
    config_path = _smoke_config(tmp_path)
    cfg = load_config(config_path)
    assert isinstance(cfg, MinerConfig)
    assert cfg.data.mode == "synthetic"
    assert cfg.gp.population_size == 6
    assert cfg.fitness.cost_rate == 0.003
    assert cfg.run_id == "test-run"


def test_load_config_synthetic_defaults():
    data = DataConfig()
    assert data.mode == "synthetic"
    assert data.synthetic_seed == 1234


# ---------------------------------------------------------------------------
# Panel building
# ---------------------------------------------------------------------------


def test_build_panel_synthetic_returns_six_fields(tmp_path):
    cfg = load_config(_smoke_config(tmp_path))
    panel, fwd = build_panel(cfg)
    assert set(panel.keys()) == {
        "$open", "$high", "$low", "$close", "$volume", "$money",
    }
    for df in panel.values():
        assert df.shape == (30, 8)
    assert fwd.shape == (30, 8)


def test_build_panel_synthetic_deterministic_with_seed(tmp_path):
    cfg = load_config(_smoke_config(tmp_path))
    p1, f1 = build_panel(cfg)
    p2, f2 = build_panel(cfg)
    for field in p1:
        assert p1[field].equals(p2[field])
    assert f1.equals(f2)


def test_build_panel_pit_mode_rejects_empty_uri(tmp_path):
    cfg = MinerConfig(
        data=DataConfig(mode="pit", pit_provider_uri="", delisted_registry_path=""),
        gp=load_config(_smoke_config(tmp_path)).gp,
        fitness=load_config(_smoke_config(tmp_path)).fitness,
        output_dir=tmp_path,
    )
    with pytest.raises(ValueError, match="pit_provider_uri"):
        build_panel(cfg)


def test_build_panel_unknown_mode_raises(tmp_path):
    smoke = load_config(_smoke_config(tmp_path))
    cfg = MinerConfig(
        data=DataConfig(mode="bogus"),
        gp=smoke.gp,
        fitness=smoke.fitness,
        output_dir=tmp_path,
    )
    with pytest.raises(ValueError, match="data.mode"):
        build_panel(cfg)


# ---------------------------------------------------------------------------
# run_mining
# ---------------------------------------------------------------------------


def test_run_mining_writes_expected_files(tmp_path):
    cfg = load_config(_smoke_config(tmp_path))
    result = run_mining(cfg)
    run_dir = result.output_dir
    assert (run_dir / "factor_pool.parquet").is_file()
    assert (run_dir / "factor_expressions.json").is_file()
    assert (run_dir / "gp_history.json").is_file()
    assert (run_dir / "config.yaml").is_file()


def test_run_mining_history_records_each_generation(tmp_path):
    cfg = load_config(_smoke_config(tmp_path))
    result = run_mining(cfg)
    history_path = result.output_dir / "gp_history.json"
    data = json.loads(history_path.read_text(encoding="utf-8"))
    assert len(data) == 2  # n_generations from smoke.yaml
    assert data[0]["gen"] == 0
    assert data[1]["gen"] == 1


def test_run_mining_same_seed_identical_pool(tmp_path):
    cfg1 = load_config(_smoke_config(tmp_path))
    # Different run_id per run so output dirs don't collide
    cfg2 = MinerConfig(
        data=cfg1.data, gp=cfg1.gp, fitness=cfg1.fitness,
        output_dir=cfg1.output_dir, run_id="test-run-2",
    )
    r1 = run_mining(cfg1)
    r2 = run_mining(cfg2)

    h1 = sorted(hash(e.expr) for e in r1.pool.all_entries())
    h2 = sorted(hash(e.expr) for e in r2.pool.all_entries())
    assert h1 == h2

    by_h1 = {hash(e.expr): e.fitness for e in r1.pool.all_entries()}
    by_h2 = {hash(e.expr): e.fitness for e in r2.pool.all_entries()}
    for h in by_h1:
        assert by_h1[h] == pytest.approx(by_h2[h], abs=1e-12)


def test_run_mining_pool_contains_at_least_one_entry(tmp_path):
    cfg = load_config(_smoke_config(tmp_path))
    result = run_mining(cfg)
    # Random init populates ~6 expressions; most are valid on synthetic data
    assert len(result.pool) >= 1


# ---------------------------------------------------------------------------
# CLI subprocess smoke
# ---------------------------------------------------------------------------


def test_miner_cli_runs_to_exit_zero(tmp_path):
    config_path = _smoke_config(tmp_path)
    result = subprocess.run(
        [sys.executable, "-m", "src.factor_mining.miner", str(config_path)],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[3]),
        timeout=120,
    )
    assert result.returncode == 0, (
        f"miner CLI failed: stderr={result.stderr!r}"
    )
    assert "Run complete" in result.stdout


# ---------------------------------------------------------------------------
# D5 strict gate
# ---------------------------------------------------------------------------


def test_miner_imports_pit_only_inside_pit_branch():
    """miner.py MAY import PITDataProvider, but only inside the
    `data.mode == 'pit'` branch (lazy import). At module load it does
    not pull qlib."""
    import src.factor_mining.miner as mod

    src = inspect.getsource(mod)
    # No direct qlib usage anywhere
    assert "from qlib" not in src
    assert "qlib.data" not in src
    assert "qlib.init" not in src
    # PIT import lives only in _build_pit_panel
    assert "from src.pit.query import PITDataProvider" in src
