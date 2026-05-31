"""Tests for ``web.operator_ui.bundle_health`` (FU-8).

The Streamlit rendering wrapper is the thinnest possible layer
around the pure ``summarise_bundle_health`` function; we test the
pure function dimensionally and use a stub ``st`` object to verify
the wrapper's plumbing without depending on a live Streamlit app.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from web.operator_ui.bundle_health import (  # noqa: E402
    BundleHealthSummary,
    _expand_env,
    render_bundle_health_banner,
    resolve_default_provider_uri,
    summarise_bundle_health,
)


def _make_bundle(
    tmp_path: Path,
    *,
    coverage_end: str = "2026-03-06",
    instrument_count: int = 4128,
    instruments: list[str] | None = None,
    calendar_days: int = 100,
) -> Path:
    """Stub a minimal bundle layout that ``inspect_provider_metadata``
    can parse."""
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    # validation.json fields the inspector reads.
    (bundle / "validation.json").write_text(
        json.dumps({
            "coverage_start_date": "2010-01-01",
            "coverage_end_date": coverage_end,
            "instrument_count": instrument_count,
            "health": "ok",
        }),
        encoding="utf-8",
    )
    # calendars/day.txt (one date per line so calendar_count is non-zero).
    (bundle / "calendars").mkdir()
    cal_lines = "\n".join(
        f"2024-01-{i:02d}" for i in range(1, calendar_days + 1)
    )
    (bundle / "calendars" / "day.txt").write_text(cal_lines, encoding="utf-8")
    # instruments/*.txt so instrument_universes is non-empty.
    (bundle / "instruments").mkdir()
    universes = instruments if instruments is not None else ["all"]
    for name in universes:
        (bundle / "instruments" / f"{name}.txt").write_text("SH000001\n")
    return bundle


# ---------------------------------------------------------------------------
# summarise_bundle_health — pure
# ---------------------------------------------------------------------------


class SummariseBundleHealthTests(unittest.TestCase):
    def test_empty_provider_uri_unconfigured(self):
        for value in ("", "   ", None):
            with self.subTest(value=value):
                s = summarise_bundle_health(value)
                self.assertEqual(s.status, "unconfigured")
                self.assertEqual(s.provider_uri, "")
                self.assertIsNone(s.tail_date)
                self.assertIsNone(s.instrument_count)
                self.assertIn("No bundle configured", s.message)

    def test_missing_path_error_status(self):
        s = summarise_bundle_health("/this/path/does/not/exist")
        self.assertEqual(s.status, "error")
        self.assertIn("does not exist", s.message)
        # Even on error, the provider_uri is echoed so the operator
        # can read what they typed (subject to platform-native path
        # normalization that the banner shares with runtime — Codex
        # P2 on PR #169 expanduser).
        self.assertEqual(
            Path(s.provider_uri),
            Path("/this/path/does/not/exist"),
        )

    def test_clean_bundle_ok_status(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            bundle = _make_bundle(Path(td))
            s = summarise_bundle_health(str(bundle))
            self.assertEqual(s.status, "ok")
            self.assertEqual(s.tail_date, "2026-03-06")
            self.assertEqual(s.instrument_count, 4128)
            self.assertIn("tail_date 2026-03-06", s.message)
            self.assertIn("4128 instruments", s.message)

    def test_bundle_with_warnings_marked_warning(self):
        """A bundle that's reachable but missing calendar / universes
        → metadata has warnings → banner status = warning."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            bundle = Path(td) / "minimal_bundle"
            bundle.mkdir()
            (bundle / "validation.json").write_text(
                json.dumps({"coverage_end_date": "2026-03-06"}),
                encoding="utf-8",
            )
            # No calendars/ or instruments/ → inspector emits warnings
            s = summarise_bundle_health(str(bundle))
            self.assertEqual(s.status, "warning")
            self.assertIn("warnings:", s.message)

    def test_bundle_with_no_metadata_files_warning(self):
        """A directory that exists but has no manifest / validation /
        calendars at all → warnings (operator pasted the wrong path,
        most likely)."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            empty = Path(td) / "no_metadata_bundle"
            empty.mkdir()
            s = summarise_bundle_health(str(empty))
            self.assertEqual(s.status, "warning")


# ---------------------------------------------------------------------------
# _expand_env
# ---------------------------------------------------------------------------


class ExpandEnvTests(unittest.TestCase):
    def test_literal_passthrough(self):
        self.assertEqual(_expand_env("D:/qlib_data/my_cn_data"), "D:/qlib_data/my_cn_data")

    def test_simple_var(self):
        with patch.dict(os.environ, {"BUNDLE_TEST": "/path"}, clear=False):
            self.assertEqual(_expand_env("${BUNDLE_TEST}"), "/path")

    def test_var_with_default(self):
        # Var unset → default
        env_without = {
            k: v for k, v in os.environ.items() if k != "BUNDLE_TEST"
        }
        with patch.dict(os.environ, env_without, clear=True):
            self.assertEqual(
                _expand_env("${BUNDLE_TEST:-/fallback}"),
                "/fallback",
            )

    def test_var_unresolved_returns_empty(self):
        """Unresolved ``${VAR}`` (no default, env missing) → empty.
        Banner shows "unconfigured" rather than the literal placeholder."""
        env_without = {
            k: v for k, v in os.environ.items() if k != "BUNDLE_TEST"
        }
        with patch.dict(os.environ, env_without, clear=True):
            self.assertEqual(_expand_env("${BUNDLE_TEST}"), "")


# ---------------------------------------------------------------------------
# resolve_default_provider_uri
# ---------------------------------------------------------------------------


class ResolveDefaultProviderUriTests(unittest.TestCase):
    def test_missing_config_returns_empty(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            ghost = Path(td) / "config_does_not_exist.yaml"
            self.assertEqual(resolve_default_provider_uri(ghost), "")

    def test_literal_provider_uri(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "config.yaml"
            cfg.write_text("provider_uri: /path/to/bundle\n")
            # Platform-native path comparison (Path() normalisation
            # changes ``/`` to ``\\`` on Windows); the value is
            # semantically the same.
            self.assertEqual(
                Path(resolve_default_provider_uri(cfg)),
                Path("/path/to/bundle"),
            )

    def test_provider_uri_with_env_var(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "config.yaml"
            cfg.write_text("provider_uri: ${QLIB_BUNDLE_TEST}\n")
            with patch.dict(
                os.environ, {"QLIB_BUNDLE_TEST": "/env/path"}, clear=False,
            ):
                self.assertEqual(
                    Path(resolve_default_provider_uri(cfg)),
                    Path("/env/path"),
                )

    def test_provider_uri_with_tilde_expanded(self):
        """Codex P2 on PR #169: configs like
        ``provider_uri: ~/qlib_data`` were treated as a literal
        relative path and the banner showed a false red error.
        After the fix, ``~`` expands to the user's home (matching
        the runtime's ``init_qlib_canonical`` resolution)."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "config.yaml"
            cfg.write_text("provider_uri: ~/qlib_data\n")
            result = resolve_default_provider_uri(cfg)
            # Tilde must NOT survive — ``~/qlib_data`` should expand
            # to ``<home>/qlib_data``.
            self.assertNotIn("~", result)
            self.assertTrue(
                result.endswith("qlib_data")
                or result.endswith("qlib_data" + os.sep),
                f"expected expanded path to end with 'qlib_data', got {result!r}",
            )

    def test_missing_provider_uri_field_returns_empty(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "config.yaml"
            cfg.write_text("instruments: csi300\n")  # no provider_uri
            self.assertEqual(resolve_default_provider_uri(cfg), "")

    def test_malformed_yaml_returns_empty(self):
        """Malformed config shouldn't crash the banner — degrade to
        unconfigured."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "config.yaml"
            cfg.write_text("{ this is not: valid: yaml: at all")
            self.assertEqual(resolve_default_provider_uri(cfg), "")

    def test_malformed_yaml_logs_warning(self):
        """A broken config.yaml still degrades to '' but now leaves a
        WARN trail so a broken file is distinguishable from a genuinely
        absent provider_uri (UI review P2-4). Previously the parse error
        was swallowed silently."""
        import tempfile

        from web.operator_ui import bundle_health

        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "config.yaml"
            cfg.write_text("{ this is not: valid: yaml: at all")
            with self.assertLogs(bundle_health._log.name, level="WARNING") as logs:
                result = resolve_default_provider_uri(cfg)
        self.assertEqual(result, "")
        self.assertEqual(len(logs.records), 1)
        msg = logs.records[0].getMessage()
        self.assertIn("treating as unconfigured", msg)
        self.assertIn(str(cfg), msg)


# ---------------------------------------------------------------------------
# render_bundle_health_banner — stub st
# ---------------------------------------------------------------------------


class _StubSt:
    """Minimal Streamlit stand-in that just captures ``caption`` calls."""

    def __init__(self):
        self.captions: list[str] = []

    def caption(self, text: str) -> None:
        self.captions.append(text)


class RenderBundleHealthBannerTests(unittest.TestCase):
    def test_renders_ok_caption(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            bundle = _make_bundle(Path(td))
            stub = _StubSt()
            summary = render_bundle_health_banner(
                provider_uri=str(bundle), st=stub,
            )
            self.assertEqual(summary.status, "ok")
            self.assertEqual(len(stub.captions), 1)
            self.assertIn("🟢", stub.captions[0])
            self.assertIn("tail_date 2026-03-06", stub.captions[0])

    def test_renders_error_caption(self):
        stub = _StubSt()
        summary = render_bundle_health_banner(
            provider_uri="/missing/bundle", st=stub,
        )
        self.assertEqual(summary.status, "error")
        self.assertEqual(len(stub.captions), 1)
        self.assertIn("🔴", stub.captions[0])

    def test_renders_unconfigured_caption_when_no_provider(self):
        """When the caller passes nothing AND no config.yaml at the
        project root, the banner shows the unconfigured state."""
        stub = _StubSt()
        # Patch resolve_default_provider_uri to return empty so we
        # don't depend on the real config.yaml at the project root.
        with patch(
            "web.operator_ui.bundle_health.resolve_default_provider_uri",
            return_value="",
        ):
            summary = render_bundle_health_banner(st=stub)
        self.assertEqual(summary.status, "unconfigured")
        self.assertIn("⚪", stub.captions[0])

    def test_explicit_provider_uri_overrides_default(self):
        """If the caller passes a provider_uri, the default lookup is
        not consulted at all."""
        stub = _StubSt()
        called = {"n": 0}

        def fake_resolve(*_a, **_kw):
            called["n"] += 1
            return "/should/not/be/used"

        with patch(
            "web.operator_ui.bundle_health.resolve_default_provider_uri",
            side_effect=fake_resolve,
        ):
            render_bundle_health_banner(
                provider_uri="/explicit/path", st=stub,
            )
        self.assertEqual(called["n"], 0, "explicit URI must short-circuit")


# Type-check helper so the dataclass shape isn't accidentally
# narrowed in a future refactor.
class BundleHealthSummaryShapeTests(unittest.TestCase):
    def test_dataclass_fields_locked(self):
        import dataclasses

        names = {f.name for f in dataclasses.fields(BundleHealthSummary)}
        self.assertEqual(
            names,
            {
                "provider_uri", "status", "message", "tail_date",
                "instrument_count", "warnings", "errors",
            },
        )


if __name__ == "__main__":
    unittest.main()
