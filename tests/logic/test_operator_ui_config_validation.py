"""Unit tests for configuration validation — provider_uri and key checking."""

from __future__ import annotations

import sys as _sys
import unittest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_PROJECT_ROOT))


class ConfigValidationTests(unittest.TestCase):
    def test_empty_provider_uri_rejected(self) -> None:
        from web.operator_ui.config_forms import validate_provider_uri
        with self.assertRaises(ValueError):
            validate_provider_uri("")
        with self.assertRaises(ValueError):
            validate_provider_uri("   ")

    def test_valid_provider_uri_passes(self) -> None:
        from web.operator_ui.config_forms import validate_provider_uri
        validate_provider_uri("D:/qlib_data/my_cn_data")
        validate_provider_uri("/data/qlib_bundle")

    def test_unknown_config_keys_hard_fail(self) -> None:
        from web.operator_ui.config_forms import PIPELINE_KEYS, validate_config_keys
        config = {"provider_uri": "/data", "typo_key": 123, "another_typo": True}
        with self.assertRaises(ValueError):
            validate_config_keys(config, PIPELINE_KEYS)

    def test_valid_config_keys_pass(self) -> None:
        from web.operator_ui.config_forms import PIPELINE_KEYS, validate_config_keys
        config = {"provider_uri": "/data", "instruments": "csi300", "train_start": "2022-01-01",
                   "train_end": "2024-12-31", "valid_start": "2025-01-01", "valid_end": "2025-06-30",
                   "test_start": "2025-07-01", "test_end": "2025-12-31"}
        validate_config_keys(config, PIPELINE_KEYS)

    def test_pipeline_keys_match_pipeline_config_fields(self) -> None:
        from src.core.pipeline import PipelineConfig
        from web.operator_ui.config_forms import PIPELINE_KEYS

        expected = {field.name for field in PipelineConfig.__dataclass_fields__.values()}
        self.assertEqual(PIPELINE_KEYS, expected)

    def test_walk_forward_keys_match_config_fields_plus_runtime_keys(self) -> None:
        from src.core.walk_forward import WalkForwardConfig
        from web.operator_ui.config_forms import WALK_FORWARD_KEYS

        expected = {
            field.name for field in WalkForwardConfig.__dataclass_fields__.values()
        } | {"provider_uri", "region"}
        self.assertEqual(WALK_FORWARD_KEYS, expected)

    def test_tushare_provider_keys_match_config_fields(self) -> None:
        from src.data.tushare.provider_bundle import TushareQlibProviderBundleConfig
        from web.operator_ui.config_forms import TUSHARE_PROVIDER_KEYS

        expected = {
            field.name
            for field in TushareQlibProviderBundleConfig.__dataclass_fields__.values()
        }
        self.assertEqual(TUSHARE_PROVIDER_KEYS, expected)


class LazyImportTests(unittest.TestCase):
    """Regression for bug.md P2-2: ``config_forms`` previously
    triggered ``src.core.pipeline``/``src.core.walk_forward``/
    ``src.data.tushare.provider_bundle`` imports at module load,
    each of which transitively imports qlib. Streamlit imports every
    page module to build the sidebar, so the UI would crash on any
    machine without qlib. The lazy ``__getattr__`` (PEP 562) defers
    those imports until first key-set access.
    """

    def test_importing_config_forms_does_not_load_heavy_configs(self) -> None:
        """A fresh subprocess that imports ``config_forms`` must NOT
        end up with ``src.core.pipeline`` etc. in ``sys.modules`` —
        proves the import-time cost is bounded to the lazy hook
        itself, not the transitive qlib dependency chain.
        """
        import subprocess

        script = (
            "import sys\n"
            "import web.operator_ui.config_forms  # noqa: F401\n"
            "heavy = ['src.core.pipeline', 'src.core.walk_forward',\n"
            "         'src.data.tushare.provider_bundle']\n"
            "loaded = [m for m in heavy if m in sys.modules]\n"
            "if loaded:\n"
            "    raise SystemExit(f'eagerly loaded: {loaded}')\n"
        )
        result = subprocess.run(
            [_sys.executable, "-c", script],
            capture_output=True,
            text=True,
            cwd=str(_PROJECT_ROOT),
        )
        self.assertEqual(
            result.returncode, 0,
            f"config_forms eagerly loaded a heavy config module — "
            f"P2-2 regression. stderr: {result.stderr}",
        )

    def test_pipeline_keys_lookup_triggers_lazy_load(self) -> None:
        """The deferred load DOES happen on first access — pin the
        contract by checking the module appears in ``sys.modules``
        after touching the lazy attr."""
        import importlib
        import sys

        # Pre-emptively evict any cached module from a prior test
        # so this assertion observes a fresh load.
        for mod_name in (
            "src.core.pipeline",
            "web.operator_ui.config_forms",
        ):
            sys.modules.pop(mod_name, None)
        cf = importlib.import_module("web.operator_ui.config_forms")
        self.assertNotIn(
            "src.core.pipeline", sys.modules,
            "import-time leak: pipeline module loaded before key-set access",
        )
        _ = cf.PIPELINE_KEYS  # trigger lazy hook
        self.assertIn(
            "src.core.pipeline", sys.modules,
            "lazy hook did not load pipeline module on first access",
        )


if __name__ == "__main__":
    unittest.main()
