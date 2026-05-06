"""Governance tests for opt-in Tushare OHLCV provider publishing."""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class TushareProviderOptInBoundaryTests(unittest.TestCase):
    def test_default_config_does_not_switch_provider_uri_to_tushare_bundle(self) -> None:
        raw = yaml.safe_load((PROJECT_ROOT / "config.yaml").read_text(encoding="utf-8"))
        self.assertIsInstance(raw, dict)
        provider_uri = str(raw.get("provider_uri", ""))
        self.assertNotIn("tushare", provider_uri.lower())
        self.assertNotIn("qlib_tushare", provider_uri.lower())

    def test_core_runtime_does_not_import_tushare_provider_publisher(self) -> None:
        offenders: list[str] = []
        for path in (PROJECT_ROOT / "src" / "core").glob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "src.data.tushare.provider_bundle" in text or "ingest_tushare_qlib_provider" in text:
                offenders.append(str(path.relative_to(PROJECT_ROOT)))
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
