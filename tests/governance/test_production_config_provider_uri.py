"""Governance: a production config's ``provider_uri`` SHALL NOT point at a
Tushare raw / non-production bundle.

Re-homed from the retired ``test_tushare_provider_opt_in_boundary.py`` (the
Tushare *publisher* it guarded was retired in unify U3). The assertion itself is
a permanent config-correctness guard, independent of the publisher: the
production ``config.yaml`` must point ``provider_uri`` at the production PIT
bundle, never at a ``tushare_raw`` staging dir or a ``qlib_tushare`` bundle.
"""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ProductionConfigProviderUriTests(unittest.TestCase):
    def test_config_yaml_provider_uri_is_not_a_tushare_bundle(self) -> None:
        raw = yaml.safe_load((PROJECT_ROOT / "config.yaml").read_text(encoding="utf-8"))
        self.assertIsInstance(raw, dict)
        provider_uri = str(raw.get("provider_uri", "")).lower()
        self.assertNotIn("tushare", provider_uri)
        self.assertNotIn("qlib_tushare", provider_uri)


if __name__ == "__main__":
    unittest.main()
