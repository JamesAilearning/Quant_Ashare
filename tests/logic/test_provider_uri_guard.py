"""provider_uri fail-loud existence guard (P4).

A missing / misconfigured ``provider_uri`` (e.g. the Windows-default
``${QUANT_PROVIDER_URI:-D:/qlib_data/my_cn_data_pit}`` on a Linux/Mac box that
never set the env var) must fail loud with a clear "set QUANT_PROVIDER_URI"
message BEFORE qlib touches the path — not as an obscure qlib error at first
data access. The operator-UI health banner already checked this; these tests
pin the shared helper plus the CLI/pipeline pre-init guards that now reuse it.

All cases here are qlib-free: the guards short-circuit before any qlib init, so
no bundle is required.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.core.qlib_runtime import (
    check_provider_uri,
    provider_uri_guard_message,
)


class CheckProviderUriTests(unittest.TestCase):
    """The pure filesystem precondition shared by UI + CLI/pipeline."""

    def test_missing_path_reports_does_not_exist_and_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ghost = str(Path(tmp) / "no_such_bundle")
            status = check_provider_uri(ghost)
        self.assertIsNotNone(status.error)
        assert status.error is not None  # narrow for the type checker
        self.assertIn("does not exist", status.error)
        self.assertTrue(status.missing)

    def test_file_is_not_a_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "a_file"
            f.write_text("not a bundle", encoding="utf-8")
            status = check_provider_uri(str(f))
        self.assertIsNotNone(status.error)
        assert status.error is not None
        self.assertIn("must be a directory", status.error)
        # Exists, just wrong type -> NOT "missing" (UI keeps reading; CLI still
        # fails loud).
        self.assertFalse(status.missing)

    def test_existing_directory_is_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            status = check_provider_uri(tmp)
        self.assertIsNone(status.error)
        self.assertFalse(status.missing)


class ProviderUriGuardMessageTests(unittest.TestCase):
    """The CLI/pipeline-facing message wrapper (base message + fix hint)."""

    def test_missing_path_message_includes_hint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ghost = str(Path(tmp) / "no_such_bundle")
            msg = provider_uri_guard_message(ghost)
        self.assertIsNotNone(msg)
        assert msg is not None
        self.assertIn("does not exist", msg)
        self.assertIn("QUANT_PROVIDER_URI", msg)

    def test_existing_directory_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(provider_uri_guard_message(tmp))

    def test_empty_defers_to_required_check(self) -> None:
        # Empty is the canonical-config layer's concern ("provider_uri is
        # required") — the guard must not pre-empt it with "does not exist: .".
        self.assertIsNone(provider_uri_guard_message(""))
        self.assertIsNone(provider_uri_guard_message("   "))

    def test_tilde_is_expanded_before_checking(self) -> None:
        # A bundle reachable via ``~`` must not false-positive: the guard
        # expanduser-s to match what _normalize_provider_uri hands qlib.
        with tempfile.TemporaryDirectory() as home:
            bundle = Path(home) / "qlib_data"
            bundle.mkdir()
            with patch.dict("os.environ", {"HOME": home, "USERPROFILE": home}):
                self.assertIsNone(provider_uri_guard_message("~/qlib_data"))


class RecommendGuardTests(unittest.TestCase):
    """daily-recommend CLI: fail loud BEFORE qlib init."""

    def _config(self, provider_uri: str):
        from src.inference.daily_recommend import RecommendationConfig
        return RecommendationConfig(
            model_path="m",
            provider_uri=provider_uri,
            delisted_registry_path="r",
            fit_start="2018-01-02",
            fit_end="2023-12-20",
        )

    def test_missing_provider_raises_before_init(self) -> None:
        from src.inference.daily_recommend import DailyRecommendationError, recommend
        with tempfile.TemporaryDirectory() as tmp:
            ghost = str(Path(tmp) / "no_such_bundle")
            with patch(
                "src.inference.daily_recommend.init_qlib_canonical",
            ) as mock_init:
                with self.assertRaisesRegex(
                    DailyRecommendationError, "does not exist",
                ):
                    recommend(self._config(ghost))
            # The guard must run BEFORE qlib init — proving the error is the
            # clear message, not a downstream qlib crash.
            mock_init.assert_not_called()


class PipelineGuardTests(unittest.TestCase):
    """walk-forward / pipeline: fail loud BEFORE run dir + qlib init."""

    def test_missing_provider_raises_before_init(self) -> None:
        from src.core.pipeline import Pipeline, PipelineConfig, PipelineError
        with tempfile.TemporaryDirectory() as tmp:
            ghost = str(Path(tmp) / "no_such_bundle")
            cfg = PipelineConfig(provider_uri=ghost, output_dir=tmp)
            with patch("src.core.pipeline.init_qlib_canonical") as mock_init:
                with self.assertRaisesRegex(PipelineError, "does not exist"):
                    Pipeline.run(cfg)
            mock_init.assert_not_called()


class UiBannerNoRegressionTests(unittest.TestCase):
    """inspect_provider_metadata must keep its exact messages now that it
    delegates the existence / directory check to the shared helper."""

    def test_missing_path_message_and_early_return(self) -> None:
        from web.operator_ui.training_guards import inspect_provider_metadata
        with tempfile.TemporaryDirectory() as tmp:
            ghost = str(Path(tmp) / "no_such_bundle")
            meta = inspect_provider_metadata(ghost)
        self.assertEqual(
            meta.errors, (f"provider_uri does not exist: {Path(ghost)}",),
        )
        # Early-return contract: nothing read from a non-existent path.
        self.assertEqual(meta.calendar_dates, ())
        self.assertEqual(meta.warnings, ())

    def test_file_path_reports_not_a_directory(self) -> None:
        from web.operator_ui.training_guards import inspect_provider_metadata
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "a_file"
            f.write_text("not a bundle", encoding="utf-8")
            meta = inspect_provider_metadata(str(f))
        # The "must be a directory" error is present; the function still falls
        # through to its best-effort reads (which find nothing), exactly as
        # before the helper extraction.
        self.assertIn(f"provider_uri must be a directory: {f}", meta.errors)


if __name__ == "__main__":
    unittest.main()
