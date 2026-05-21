"""Regression tests for explicit operator UI artifact read issues."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from web.operator_ui._path_guard import output_path
from web.operator_ui.artifact_reader import (
    ArtifactReadIssue,
    read_bytes_artifact,
    read_json_artifact,
)


class OperatorUiArtifactReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        output_path("operator_ui").mkdir(parents=True, exist_ok=True)

    def test_missing_json_artifact_is_empty_without_issue(self) -> None:
        with tempfile.TemporaryDirectory(dir=output_path("operator_ui")) as tmp_dir:
            result = read_json_artifact(Path(tmp_dir) / "missing.json")

        self.assertEqual(result.value, {})
        self.assertIsNone(result.issue)

    def test_malformed_json_artifact_returns_displayable_issue(self) -> None:
        with tempfile.TemporaryDirectory(dir=output_path("operator_ui")) as tmp_dir:
            artifact_path = Path(tmp_dir) / "metrics.json"
            artifact_path.write_text("{not-json", encoding="utf-8")

            result = read_json_artifact(artifact_path, artifact_name="metrics.json")

        self.assertEqual(result.value, {})
        self.assertIsInstance(result.issue, ArtifactReadIssue)
        assert result.issue is not None
        self.assertEqual(result.issue.artifact_name, "metrics.json")
        self.assertEqual(result.issue.error_type, "JSONDecodeError")
        self.assertIn("metrics.json", result.issue.path)

    def test_non_object_json_artifact_returns_shape_issue(self) -> None:
        with tempfile.TemporaryDirectory(dir=output_path("operator_ui")) as tmp_dir:
            artifact_path = Path(tmp_dir) / "metrics.json"
            artifact_path.write_text("[1, 2, 3]", encoding="utf-8")

            result = read_json_artifact(artifact_path)

        self.assertEqual(result.value, {})
        self.assertIsNotNone(result.issue)
        assert result.issue is not None
        self.assertEqual(result.issue.error_type, "InvalidArtifactShape")

    def test_outside_output_path_returns_path_guard_issue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            artifact_path = Path(tmp_dir) / "config.yaml"
            artifact_path.write_text("provider_uri: x", encoding="utf-8")

            result = read_bytes_artifact(artifact_path, artifact_name="config.yaml")

        self.assertEqual(result.value, b"")
        self.assertIsNotNone(result.issue)
        assert result.issue is not None
        self.assertEqual(result.issue.error_type, "ValueError")
        self.assertIn("outside allowed roots", result.issue.message)


if __name__ == "__main__":
    unittest.main()
