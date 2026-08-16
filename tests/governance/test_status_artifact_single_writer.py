"""Governance: the run-status artifact has exactly ONE writer and NO
canonical consumer (2026-08-14-daily-update-run-status).

The artifact is observability-only. Two failure modes this pins:

* a SECOND writer appears (another module starts emitting the same file and
  the UI's "上次数据更新" section stops meaning "the orchestrator's run");
* a consumer inside ``src/`` starts reading it (observability leaking into
  canonical runtime / metrics paths — the proposal's explicit non-goal).

Source-level scan, same style as test_data_inspect_readonly: the filename
string is the seam, so every reference to it inside ``src/`` is enumerated
here. ``web/`` may read it (that is the point); ``research/`` must not.
"""

from __future__ import annotations

import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

_ALLOWED_SRC_REFERENCES = {
    # The sole writer (definition + default path + writes).
    "src/data_pipeline/daily_update.py",
}


class StatusArtifactSingleWriterTests(unittest.TestCase):

    def _src_references(self) -> dict[str, int]:
        hits: dict[str, int] = {}
        for path in (PROJECT_ROOT / "src").rglob("*.py"):
            rel = path.relative_to(PROJECT_ROOT).as_posix()
            count = path.read_text(encoding="utf-8").count("daily_update_status.json")
            if count:
                hits[rel] = count
        return hits

    def test_only_the_orchestrator_references_the_artifact_in_src(self) -> None:
        self.assertEqual(set(self._src_references()), _ALLOWED_SRC_REFERENCES)

    def test_no_consumer_reaches_the_artifact_via_the_api(self) -> None:
        # A literal scan alone misses `from src.data_pipeline.daily_update
        # import default_status_path` — the importer holds no filename
        # literal yet reads/writes the artifact through the exported helper
        # (codex #434 r24). Track the API names too, in BOTH layers.
        api_names = ("default_status_path", "_write_status",
                     "_record_status", "_status_tmp_path", "STATUS_FILENAME")
        writer_rel = "src/data_pipeline/daily_update.py"
        for layer in ("src", "research"):
            for path in (PROJECT_ROOT / layer).rglob("*.py"):
                rel = path.relative_to(PROJECT_ROOT).as_posix()
                if rel == writer_rel:
                    continue
                text = path.read_text(encoding="utf-8")
                if "daily_update" not in text:
                    continue
                for name in api_names:
                    with self.subTest(module=rel, api=name):
                        self.assertNotIn(
                            name, text,
                            f"{rel} 经导出的 {name!r} 触达状态工件 —— "
                            f"单一写者边界被 API 层绕过")

    def test_research_layer_never_reads_it(self) -> None:
        for path in (PROJECT_ROOT / "research").rglob("*.py"):
            self.assertNotIn(
                "daily_update_status.json",
                path.read_text(encoding="utf-8"),
                f"{path} references the run-status artifact",
            )


if __name__ == "__main__":
    unittest.main()
