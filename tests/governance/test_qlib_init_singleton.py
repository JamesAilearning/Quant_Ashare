"""Governance tests for the canonical qlib runtime entry point.

These tests do NOT require qlib itself to be importable. They exercise
the re-initialization guard logic by calling the entry point with
structurally valid configs and catching the inner ImportError path as
a non-blocking signal on environments without qlib installed.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core import qlib_runtime  # noqa: E402
from src.core.qlib_runtime import (  # noqa: E402
    QlibRuntimeConfig,
    QlibRuntimeInitError,
    _qlib_session_mismatch,
    _reset_canonical_qlib_runtime_for_tests,
    get_canonical_qlib_config,
    init_qlib_canonical,
    is_canonical_qlib_initialized,
)


def _qlib_importable() -> bool:
    try:
        import qlib  # noqa: F401
        return True
    except ImportError:
        return False


class QlibRuntimeConfigValidationTests(unittest.TestCase):
    def test_requires_provider_uri(self) -> None:
        with self.assertRaises(QlibRuntimeInitError):
            QlibRuntimeConfig(provider_uri="", region="cn")

    def test_requires_known_region(self) -> None:
        with self.assertRaises(QlibRuntimeInitError):
            QlibRuntimeConfig(provider_uri="D:/qlib_data/my_cn_data", region="eu")

    def test_accepts_cn_and_us(self) -> None:
        QlibRuntimeConfig(provider_uri="D:/qlib_data/my_cn_data", region="cn")
        QlibRuntimeConfig(provider_uri="D:/qlib_data/my_us_data", region="US")


class QlibRuntimeInitGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        _reset_canonical_qlib_runtime_for_tests()

    def tearDown(self) -> None:
        _reset_canonical_qlib_runtime_for_tests()

    def test_must_pass_config_instance(self) -> None:
        with self.assertRaises(QlibRuntimeInitError):
            init_qlib_canonical("not-a-config")  # type: ignore[arg-type]

    @unittest.skipUnless(_qlib_importable(), "qlib not installed in this environment")
    def test_idempotent_same_config(self) -> None:
        cfg = QlibRuntimeConfig(provider_uri="D:/qlib_data/my_cn_data", region="cn")
        init_qlib_canonical(cfg)
        # second call with the same config must not raise
        init_qlib_canonical(cfg)
        self.assertTrue(is_canonical_qlib_initialized())
        self.assertEqual(get_canonical_qlib_config(), cfg)

    @unittest.skipUnless(_qlib_importable(), "qlib not installed in this environment")
    def test_conflicting_config_raises(self) -> None:
        cfg1 = QlibRuntimeConfig(provider_uri="D:/qlib_data/my_cn_data", region="cn")
        cfg2 = QlibRuntimeConfig(provider_uri="D:/qlib_data/other_data", region="cn")
        init_qlib_canonical(cfg1)
        with self.assertRaises(QlibRuntimeInitError):
            init_qlib_canonical(cfg2)
        # State must still reflect the first successful config.
        self.assertEqual(get_canonical_qlib_config(), cfg1)


class QlibSessionMismatchTests(unittest.TestCase):
    """Unit tests for _qlib_session_mismatch — no qlib import needed."""

    class _FakeC:
        """Minimal stand-in for qlib.config.C."""
        def __init__(self, provider_uri, region=None):
            self.provider_uri = provider_uri
            self.region = region

    def _cfg(self, path: str, region: str = "cn") -> QlibRuntimeConfig:
        return QlibRuntimeConfig(provider_uri=path, region=region)

    def test_matching_config_returns_none(self) -> None:
        import os
        cfg = self._cfg(r"D:/qlib_data/my_cn_data")
        fake_c = self._FakeC(os.path.normpath(r"D:/qlib_data/my_cn_data"))
        from qlib.constant import REG_CN  # type: ignore[import-not-found]
        result = _qlib_session_mismatch(fake_c, cfg, REG_CN)
        self.assertIsNone(result)

    def test_provider_uri_mismatch_detected(self) -> None:
        cfg = self._cfg(r"D:/qlib_data/my_cn_data")
        fake_c = self._FakeC(r"D:/qlib_data/other_data")
        result = _qlib_session_mismatch(fake_c, cfg, object())
        self.assertIsNotNone(result)
        assert result is not None
        self.assertIn("provider_uri mismatch", result)

    def test_dict_provider_uri_uses_day_key(self) -> None:
        import os
        cfg = self._cfg(r"D:/qlib_data/my_cn_data")
        # qlib sometimes stores provider_uri as {"day": path, "1min": ...}
        fake_c = self._FakeC({
            "day": os.path.normpath(r"D:/qlib_data/my_cn_data"),
            "1min": r"D:/qlib_data/1min_data",
        })
        from qlib.constant import REG_CN  # type: ignore[import-not-found]
        result = _qlib_session_mismatch(fake_c, cfg, REG_CN)
        self.assertIsNone(result)

    def test_none_provider_uri_returns_description(self) -> None:
        cfg = self._cfg(r"D:/qlib_data/my_cn_data")
        fake_c = self._FakeC(None)
        result = _qlib_session_mismatch(fake_c, cfg, object())
        self.assertIsNotNone(result)

    def test_region_mismatch_detected(self) -> None:
        import os
        from qlib.constant import REG_CN, REG_US  # type: ignore[import-not-found]
        cfg = self._cfg(r"D:/qlib_data/my_cn_data", region="us")
        fake_c = self._FakeC(os.path.normpath(r"D:/qlib_data/my_cn_data"), region=REG_CN)
        result = _qlib_session_mismatch(fake_c, cfg, REG_US)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertIn("region mismatch", result)


class QlibRuntimeResetHelperBoundaryTests(unittest.TestCase):
    """Boundary check: _reset_canonical_qlib_runtime_for_tests is test-only."""

    def test_reset_helper_is_only_imported_from_tests(self) -> None:
        offenders: list[str] = []
        src_root = PROJECT_ROOT / "src"
        for py_file in src_root.rglob("*.py"):
            text = py_file.read_text(encoding="utf-8")
            if "_reset_canonical_qlib_runtime_for_tests" in text:
                # Allow the definition itself in src/core/qlib_runtime.py.
                if py_file.name == "qlib_runtime.py":
                    continue
                offenders.append(str(py_file.relative_to(PROJECT_ROOT)))
        self.assertEqual(
            offenders,
            [],
            msg=f"Test-only reset helper leaked into production code: {offenders}",
        )

    def test_module_exposes_expected_owner_constant(self) -> None:
        self.assertEqual(qlib_runtime.CANONICAL_QLIB_INIT_OWNER, "src.core.qlib_runtime")


if __name__ == "__main__":
    unittest.main()
