"""Governance: the QUANT_* path env-vars MUST default to the current paths.

Phase 1 (ops P1-1) parameterized 9 production data/artifact paths behind
``QUANT_*`` env vars — 4 in YAML (``${VAR:-default}``) and 5 in Python
(``os.environ.get(VAR, default)``). The hard invariant of that change is
**zero behaviour change when no env var is set**: each default must equal the
path that was previously hardcoded.

This test machine-locks that invariant (not just "no existing test went red"):
with every QUANT_* unset, every one of the 9 sites must resolve to its current
literal. If anyone edits a default, this fails immediately and names the site.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.core._yaml_loader import load_yaml_with_inheritance  # noqa: E402
from src.inference.daily_recommend import RecommendationConfig  # noqa: E402

# The 5 vars and their canonical defaults (= the pre-Phase-1 hardcoded values).
_PROVIDER = "D:/qlib_data/my_cn_data_pit"
_NAMECHANGE = "D:/qlib_data/tushare_raw/all_namechanges.parquet"
_NAME_SOURCE = "D:/qlib_data/tushare_raw/active_stocks.parquet"
_REGISTRY = "D:/qlib_data/tushare_raw/delisted_registry.parquet"
_MODEL = "D:/stock/phase_b_artifacts/alpha158_lgb_pit.pkl"

_QUANT_VARS = (
    "QUANT_PROVIDER_URI", "QUANT_NAMECHANGE_PATH", "QUANT_NAME_SOURCE",
    "QUANT_DELISTED_REGISTRY", "QUANT_MODEL_PATH",
)


def _load_cli_module():
    """Fresh-exec scripts/daily_recommend.py so its module-level QUANT_*
    defaults are read against the CURRENT environment (under a unique name so
    it is never served from sys.modules' cache)."""
    path = _PROJECT_ROOT / "scripts" / "daily_recommend.py"
    spec = importlib.util.spec_from_file_location("_dr_cli_under_test", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class PathParamDefaultsTests(unittest.TestCase):
    """No QUANT_* env -> every parameterized path resolves to its current value."""

    def setUp(self) -> None:
        self._saved = {v: os.environ.pop(v, None) for v in _QUANT_VARS}

    def tearDown(self) -> None:
        for v, old in self._saved.items():
            if old is None:
                os.environ.pop(v, None)
            else:
                os.environ[v] = old

    # --- YAML side (4) ---
    def test_config_yaml_provider_uri_default(self) -> None:
        cfg = load_yaml_with_inheritance(_PROJECT_ROOT / "config.yaml")
        self.assertEqual(cfg["provider_uri"], _PROVIDER)

    def test_config_smoke_provider_uri_default(self) -> None:
        cfg = load_yaml_with_inheritance(_PROJECT_ROOT / "config_smoke.yaml")
        self.assertEqual(cfg["provider_uri"], _PROVIDER)

    def test_config_walk_provider_and_namechange_defaults(self) -> None:
        cfg = load_yaml_with_inheritance(_PROJECT_ROOT / "config_walk.yaml")
        self.assertEqual(cfg["provider_uri"], _PROVIDER)
        self.assertEqual(cfg["namechange_path"], _NAMECHANGE)

    # --- Python side (5) ---
    def test_recommendation_config_name_source_default(self) -> None:
        cfg = RecommendationConfig(
            model_path="m", provider_uri="p", delisted_registry_path="r",
            fit_start="2018-01-02", fit_end="2023-12-20",
        )
        self.assertEqual(cfg.name_source_parquet, _NAME_SOURCE)

    def test_cli_constant_defaults(self) -> None:
        mod = _load_cli_module()
        self.assertEqual(mod._DEFAULT_MODEL, _MODEL)
        self.assertEqual(mod._DEFAULT_PROVIDER, _PROVIDER)
        self.assertEqual(mod._DEFAULT_REGISTRY, _REGISTRY)
        self.assertEqual(mod._DEFAULT_NAME_SOURCE, _NAME_SOURCE)

    def test_env_override_reaches_dataclass(self) -> None:
        # And a SET var actually overrides (proves the wiring, not just default).
        os.environ["QUANT_NAME_SOURCE"] = "E:/custom/active.parquet"
        cfg = RecommendationConfig(
            model_path="m", provider_uri="p", delisted_registry_path="r",
            fit_start="2018-01-02", fit_end="2023-12-20",
        )
        self.assertEqual(cfg.name_source_parquet, "E:/custom/active.parquet")


if __name__ == "__main__":
    unittest.main()
