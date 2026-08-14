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

import sys
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

    def test_research_layer_never_reads_it(self) -> None:
        for path in (PROJECT_ROOT / "research").rglob("*.py"):
            self.assertNotIn(
                "daily_update_status.json",
                path.read_text(encoding="utf-8"),
                f"{path} references the run-status artifact",
            )


if __name__ == "__main__":
    unittest.main()
