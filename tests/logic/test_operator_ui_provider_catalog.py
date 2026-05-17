"""Unit tests for UI-managed provider catalog discovery."""

from __future__ import annotations

import json
import sys as _sys
import tempfile
import unittest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_PROJECT_ROOT))


def _write_provider(result_root: Path, job_id: str, start: str, end: str) -> Path:
    provider = result_root / job_id / "qlib_provider"
    (provider / "calendars").mkdir(parents=True)
    (provider / "instruments").mkdir()
    (provider / "calendars" / "day.txt").write_text(
        "\n".join([start, end]),
        encoding="utf-8",
    )
    (provider / "instruments" / "all.txt").write_text(
        f"SH600000\t{start}\t{end}\n",
        encoding="utf-8",
    )
    (result_root / job_id / "validation.json").write_text(
        json.dumps({
            "health": "ok",
            "coverage_start_date": start,
            "coverage_end_date": end,
            "calendar_count": 2,
            "instrument_count": 1,
            "row_count": 2,
        }),
        encoding="utf-8",
    )
    return provider


class OperatorUiProviderCatalogTests(unittest.TestCase):
    def test_catalog_lists_ui_managed_qlib_providers_newest_first(self) -> None:
        from web.operator_ui.provider_catalog import list_provider_catalog_entries

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            older = _write_provider(
                root,
                "tushare_provider_20260516_000000_aaaaaaaa",
                "2025-01-02",
                "2025-01-31",
            )
            newer = _write_provider(
                root,
                "tushare_provider_20260517_000000_bbbbbbbb",
                "2025-02-03",
                "2025-02-28",
            )

            entries = list_provider_catalog_entries(root)

        self.assertEqual([entry.job_id for entry in entries], [
            "tushare_provider_20260517_000000_bbbbbbbb",
            "tushare_provider_20260516_000000_aaaaaaaa",
        ])
        self.assertEqual(entries[0].provider_path, newer.resolve())
        self.assertEqual(entries[1].provider_path, older.resolve())
        self.assertIn("2025-02-03 to 2025-02-28", entries[0].label)
        self.assertIn("ok", entries[0].label)
        self.assertIn("all", entries[0].label)

    def test_catalog_ignores_result_dirs_without_qlib_provider(self) -> None:
        from web.operator_ui.provider_catalog import list_provider_catalog_entries

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pipeline_20260517_000000_cccccccc").mkdir()

            entries = list_provider_catalog_entries(root)

        self.assertEqual(entries, [])

    def test_delete_provider_catalog_entry_removes_result_dir(self) -> None:
        from web.operator_ui.provider_catalog import delete_provider_catalog_entry

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_provider(
                root,
                "tushare_provider_20260517_000000_bbbbbbbb",
                "2025-02-03",
                "2025-02-28",
            )

            delete_provider_catalog_entry(
                "tushare_provider_20260517_000000_bbbbbbbb",
                result_root=root,
            )

            self.assertFalse(
                root.joinpath("tushare_provider_20260517_000000_bbbbbbbb").exists()
            )

    def test_delete_provider_catalog_entry_rejects_non_provider_result(self) -> None:
        from web.operator_ui.provider_catalog import (
            ProviderCatalogError,
            delete_provider_catalog_entry,
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.joinpath("pipeline_20260517_000000_cccccccc").mkdir()

            with self.assertRaises(ProviderCatalogError):
                delete_provider_catalog_entry(
                    "pipeline_20260517_000000_cccccccc",
                    result_root=root,
                )

            self.assertTrue(root.joinpath("pipeline_20260517_000000_cccccccc").is_dir())

    def test_delete_provider_catalog_entry_rejects_path_traversal(self) -> None:
        from web.operator_ui.provider_catalog import (
            ProviderCatalogError,
            delete_provider_catalog_entry,
        )

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ProviderCatalogError):
                delete_provider_catalog_entry("..\\outside", result_root=Path(tmp))


if __name__ == "__main__":
    unittest.main()
