"""Tests for the walk-forward CLI's MinedFactor wiring (Phase 6.3).

Verifies that ``scripts/run_walk_forward.py``:

- Accepts the four ``mined_factor_*`` top-level YAML keys.
- Requires ``mined_factor_pool_dir`` + ``mined_factor_delisted_registry_path``
  when ``feature_handler == "MinedFactor"``.
- Defaults ``mined_factor_pit_provider_uri`` to the top-level
  ``provider_uri`` and warns when they diverge.
- Binds via ``register_mined_factor_handler`` strictly between
  ``init_qlib_canonical`` and ``WalkForwardEngine.run``.

All tests use synthetic pool directories (FactorPool.save into tmp_path)
so the bundle's ``__post_init__`` validation passes without needing a
real PIT bundle.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.run_walk_forward import (  # noqa: E402
    _load_config,
    _maybe_build_mined_factor_bundle,
)
from src.factor_mining.expression import OperatorCall, Terminal  # noqa: E402
from src.factor_mining.factor_pool import FactorPool, PoolEntry  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _seed_pool(pool_dir: Path) -> Path:
    pool_dir.mkdir(parents=True, exist_ok=True)
    expr = OperatorCall("cs_rank", (Terminal("$volume"),))
    pool = FactorPool()
    pool.add(PoolEntry(
        expr=expr, fitness=1.0,
        ic_mean=0.05, ic_std=0.10, ir=0.5,
        rank_ic_mean=0.04, rank_ic_std=0.08, rank_ir=0.5,
        turnover_daily=0.10, coverage=0.95, n_obs_per_day_min=20,
        expr_size=2, expr_hash=hash(expr),
    ))
    pool.save(pool_dir)
    return pool_dir


def _write_yaml(path: Path, lines: list[str]) -> Path:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _baseline_yaml_lines(provider_uri: str = "D:/qlib_data/my_cn_data") -> list[str]:
    """Minimal valid YAML for a walk-forward run (no MinedFactor)."""
    return [
        f'provider_uri: "{provider_uri}"',
        'region: "cn"',
    ]


# ---------------------------------------------------------------------------
# _load_config — schema extension
# ---------------------------------------------------------------------------


def test_load_config_with_mined_factor_keys_parses(tmp_path):
    """All four mined_factor_* keys are accepted in the YAML."""
    pool_dir = _seed_pool(tmp_path / "pool")
    cfg = _write_yaml(tmp_path / "wf.yaml", [
        *_baseline_yaml_lines(),
        'feature_handler: "MinedFactor"',
        'adjust_mode: "post_adjusted"',
        f'mined_factor_pool_dir: "{pool_dir.as_posix()}"',
        f'mined_factor_delisted_registry_path: "{(tmp_path / "registry.parquet").as_posix()}"',
        'mined_factor_pit_provider_uri: "D:/qlib_data/my_cn_data"',
        'mined_factor_universe_name_override: "csi300"',
    ])
    wf_config, qlib_config = _load_config(str(cfg))
    assert wf_config.feature_handler == "MinedFactor"
    # qlib runtime normalises the path (Windows backslashes, case);
    # just confirm it parses without error and is non-empty.
    assert str(qlib_config.provider_uri)


def test_load_config_alpha158_with_mined_factor_keys_allowed(tmp_path):
    """An Alpha158 YAML may include mined_factor_* keys (template scenario)."""
    pool_dir = _seed_pool(tmp_path / "pool")
    cfg = _write_yaml(tmp_path / "wf.yaml", [
        *_baseline_yaml_lines(),
        'feature_handler: "Alpha158"',
        f'mined_factor_pool_dir: "{pool_dir.as_posix()}"',
    ])
    wf_config, _ = _load_config(str(cfg))
    assert wf_config.feature_handler == "Alpha158"


def test_load_config_unknown_key_still_rejected(tmp_path):
    """The unknown-key strict rejection rule still fires for typos."""
    cfg = _write_yaml(tmp_path / "wf.yaml", [
        *_baseline_yaml_lines(),
        'minde_factor_pool_dir: "typo"',  # deliberate typo
    ])
    with pytest.raises(ValueError, match="Unknown config keys"):
        _load_config(str(cfg))


# ---------------------------------------------------------------------------
# _maybe_build_mined_factor_bundle — required-when-MinedFactor contract
# ---------------------------------------------------------------------------


def test_bundle_none_for_alpha158_handler(tmp_path):
    pool_dir = _seed_pool(tmp_path / "pool")
    cfg = _write_yaml(tmp_path / "wf.yaml", [
        *_baseline_yaml_lines(),
        'feature_handler: "Alpha158"',
        f'mined_factor_pool_dir: "{pool_dir.as_posix()}"',
    ])
    wf_config, qlib_config = _load_config(str(cfg))
    # Re-read raw for the bundle helper
    import yaml as _yaml

    raw = _yaml.safe_load(cfg.read_text(encoding="utf-8"))
    bundle = _maybe_build_mined_factor_bundle(raw, wf_config, qlib_config.provider_uri)
    assert bundle is None


def test_bundle_missing_pool_dir_raises(tmp_path):
    cfg = _write_yaml(tmp_path / "wf.yaml", [
        *_baseline_yaml_lines(),
        'feature_handler: "MinedFactor"',
        'adjust_mode: "post_adjusted"',
        'mined_factor_delisted_registry_path: "registry.parquet"',
    ])
    wf_config, qlib_config = _load_config(str(cfg))
    import yaml as _yaml

    raw = _yaml.safe_load(cfg.read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="mined_factor_pool_dir"):
        _maybe_build_mined_factor_bundle(raw, wf_config, qlib_config.provider_uri)


def test_bundle_missing_registry_raises(tmp_path):
    pool_dir = _seed_pool(tmp_path / "pool")
    cfg = _write_yaml(tmp_path / "wf.yaml", [
        *_baseline_yaml_lines(),
        'feature_handler: "MinedFactor"',
        'adjust_mode: "post_adjusted"',
        f'mined_factor_pool_dir: "{pool_dir.as_posix()}"',
    ])
    wf_config, qlib_config = _load_config(str(cfg))
    import yaml as _yaml

    raw = _yaml.safe_load(cfg.read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="mined_factor_delisted_registry_path"):
        _maybe_build_mined_factor_bundle(raw, wf_config, qlib_config.provider_uri)


def test_bundle_pit_uri_defaults_to_provider_uri(tmp_path):
    pool_dir = _seed_pool(tmp_path / "pool")
    cfg = _write_yaml(tmp_path / "wf.yaml", [
        'provider_uri: "/data/pit"',
        'region: "cn"',
        'feature_handler: "MinedFactor"',
        'adjust_mode: "post_adjusted"',
        f'mined_factor_pool_dir: "{pool_dir.as_posix()}"',
        f'mined_factor_delisted_registry_path: "{(tmp_path / "reg.parquet").as_posix()}"',
    ])
    wf_config, qlib_config = _load_config(str(cfg))
    import yaml as _yaml

    raw = _yaml.safe_load(cfg.read_text(encoding="utf-8"))
    bundle = _maybe_build_mined_factor_bundle(raw, wf_config, qlib_config.provider_uri)
    assert bundle is not None
    # The bundle's pit_provider_uri is the same as the (possibly
    # normalised) qlib runtime provider_uri, since the YAML did not
    # set mined_factor_pit_provider_uri explicitly.
    assert bundle.pit_provider_uri == qlib_config.provider_uri


def test_bundle_pit_uri_explicit_divergence_warns(tmp_path, caplog):
    pool_dir = _seed_pool(tmp_path / "pool")
    cfg = _write_yaml(tmp_path / "wf.yaml", [
        'provider_uri: "/data/qlib_legacy"',
        'region: "cn"',
        'feature_handler: "MinedFactor"',
        'adjust_mode: "post_adjusted"',
        f'mined_factor_pool_dir: "{pool_dir.as_posix()}"',
        f'mined_factor_delisted_registry_path: "{(tmp_path / "reg.parquet").as_posix()}"',
        'mined_factor_pit_provider_uri: "/data/qlib_pit"',
    ])
    wf_config, qlib_config = _load_config(str(cfg))
    import yaml as _yaml

    raw = _yaml.safe_load(cfg.read_text(encoding="utf-8"))
    import logging

    with caplog.at_level(logging.WARNING):
        bundle = _maybe_build_mined_factor_bundle(
            raw, wf_config, qlib_config.provider_uri,
        )
    assert bundle is not None
    assert bundle.pit_provider_uri == "/data/qlib_pit"
    assert any("differs from" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# main() call order — qlib_init → register → engine_run
# ---------------------------------------------------------------------------


def test_main_binds_handler_between_qlib_init_and_engine_run(tmp_path, monkeypatch):
    """A MinedFactor YAML drives main() to call register exactly between
    qlib init and engine run; an Alpha158 YAML does not register."""
    pool_dir = _seed_pool(tmp_path / "pool")
    cfg = _write_yaml(tmp_path / "wf.yaml", [
        *_baseline_yaml_lines(),
        'feature_handler: "MinedFactor"',
        'adjust_mode: "post_adjusted"',
        f'mined_factor_pool_dir: "{pool_dir.as_posix()}"',
        f'mined_factor_delisted_registry_path: "{(tmp_path / "reg.parquet").as_posix()}"',
        f'output_dir: "{(tmp_path / "out").as_posix()}"',
    ])
    import scripts.run_walk_forward as mod

    call_order: list[str] = []

    def _fake_init(_qlib_cfg):
        call_order.append("init_qlib_canonical")

    def _fake_register(bundle, *, replace=False):
        call_order.append(f"register_mined_factor_handler(replace={replace})")

    fake_result = MagicMock()
    fake_result.num_folds = 0
    fake_result.report_path = "x.json"

    def _fake_run(_wf_cfg, **_kwargs):
        # **_kwargs absorbs PR4's resume_mode=… so this fake stays
        # compatible across both signatures.
        call_order.append("WalkForwardEngine.run")
        return fake_result

    monkeypatch.setattr(mod, "init_qlib_canonical", _fake_init)
    monkeypatch.setattr(mod, "register_mined_factor_handler", _fake_register)
    monkeypatch.setattr(mod.WalkForwardEngine, "run", staticmethod(_fake_run))
    monkeypatch.setattr(sys, "argv", ["run_walk_forward.py", str(cfg)])

    mod.main()

    assert call_order == [
        "init_qlib_canonical",
        "register_mined_factor_handler(replace=True)",
        "WalkForwardEngine.run",
    ]


def test_main_alpha158_yaml_does_not_register(tmp_path, monkeypatch):
    """An Alpha158 YAML must not invoke register_mined_factor_handler."""
    cfg = _write_yaml(tmp_path / "wf.yaml", [
        *_baseline_yaml_lines(),
        'feature_handler: "Alpha158"',
        f'output_dir: "{(tmp_path / "out").as_posix()}"',
    ])
    import scripts.run_walk_forward as mod

    register_calls: list = []

    def _fake_init(_):
        pass

    def _fake_register(bundle, *, replace=False):
        register_calls.append((bundle, replace))

    fake_result = MagicMock()
    fake_result.num_folds = 0
    fake_result.report_path = "x.json"

    monkeypatch.setattr(mod, "init_qlib_canonical", _fake_init)
    monkeypatch.setattr(mod, "register_mined_factor_handler", _fake_register)
    monkeypatch.setattr(
        mod.WalkForwardEngine, "run",
        # **_kwargs absorbs PR4's resume_mode=… kwarg without forcing
        # this test to care about the resume policy.
        staticmethod(lambda _cfg, **_kwargs: fake_result),
    )
    monkeypatch.setattr(sys, "argv", ["run_walk_forward.py", str(cfg)])

    mod.main()
    assert register_calls == []
