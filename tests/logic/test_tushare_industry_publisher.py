"""Tests for ``src.data.tushare.industry_publisher``."""

from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.tushare.client import TushareClient
from src.data.tushare.industry_publisher import (  # noqa: E402
    SW_L2_TAXONOMY_NAME,
    TushareIndustryPublisher,
    TushareIndustryPublisherError,
    _tushare_to_qlib_instrument,
)


class _FakePandasFrame:
    """Minimal stand-in for the ``DataFrame`` rows returned by
    ``tushare.pro_api`` calls.

    We don't import pandas in tests to keep the boundary clear: the
    publisher only iterates rows, accesses columns by name, and reads
    ``len()`` — anything more would couple the test to pandas internals
    we don't control. Implements just those operations.
    """

    def __init__(self, rows: list[dict], columns: list[str]):
        self._rows = rows
        self.columns = columns

    def __len__(self):
        return len(self._rows)

    def iterrows(self):
        for idx, row in enumerate(self._rows):
            yield idx, row


class _StubClient:
    """Test double: pre-loaded responses keyed by API name."""

    def __init__(self, responses: dict):
        self._responses = responses
        self.calls: list[tuple] = []

    def call(self, api_name: str, **params):
        self.calls.append((api_name, params))
        if api_name not in self._responses:
            raise AssertionError(f"unexpected API call: {api_name}")
        resp = self._responses[api_name]
        # ``index_member`` is keyed by ``index_code`` so the stub can
        # return different members per industry; allow the response
        # entry to be a dict-of-frames to handle that.
        if isinstance(resp, dict):
            key = params.get("index_code")
            if key not in resp:
                raise AssertionError(
                    f"index_member called with unexpected index_code: {key!r}"
                )
            return resp[key]
        return resp


# ---------------------------------------------------------------------
# Code-format converter
# ---------------------------------------------------------------------


class TushareToQlibInstrumentTests(unittest.TestCase):
    """Pin the Tushare→qlib code conversion so a future refactor can't
    drop a suffix mapping silently."""

    def test_sh_dotted_becomes_prefixed(self) -> None:
        self.assertEqual(_tushare_to_qlib_instrument("600000.SH"), "SH600000")

    def test_sz_dotted_becomes_prefixed(self) -> None:
        self.assertEqual(_tushare_to_qlib_instrument("000001.SZ"), "SZ000001")

    def test_bj_dotted_becomes_prefixed(self) -> None:
        """BSE codes (added in board_heuristic earlier) must convert too."""
        self.assertEqual(_tushare_to_qlib_instrument("430047.BJ"), "BJ430047")

    def test_no_dot_returns_none(self) -> None:
        self.assertIsNone(_tushare_to_qlib_instrument("600000"))

    def test_unknown_suffix_returns_none(self) -> None:
        # Hong Kong shouldn't survive — different code space.
        self.assertIsNone(_tushare_to_qlib_instrument("00700.HK"))

    def test_non_numeric_code_returns_none(self) -> None:
        self.assertIsNone(_tushare_to_qlib_instrument("ABCDEF.SH"))

    def test_wrong_length_returns_none(self) -> None:
        self.assertIsNone(_tushare_to_qlib_instrument("60000.SH"))


# ---------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------


class PublisherValidationTests(unittest.TestCase):
    def test_rejects_bad_snapshot_date(self) -> None:
        client = _StubClient({})
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(
                TushareIndustryPublisherError, "snapshot_at"
            ):
                TushareIndustryPublisher.publish(
                    artifact_path=str(Path(tmp) / "a.csv"),
                    manifest_path=str(Path(tmp) / "a.json"),
                    snapshot_at="not-a-date",
                    client=client,
                )

    def test_rejects_empty_industry_list(self) -> None:
        """Tushare returned zero industries → publisher refuses to
        write an empty artifact."""
        client = _StubClient({
            "index_classify": _FakePandasFrame(rows=[], columns=[
                "index_code", "industry_name", "level",
            ]),
        })
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(
                TushareIndustryPublisherError, "zero industries"
            ):
                TushareIndustryPublisher.publish(
                    artifact_path=str(Path(tmp) / "a.csv"),
                    manifest_path=str(Path(tmp) / "a.json"),
                    snapshot_at="2026-04-25",
                    client=client,
                )

    def test_index_classify_missing_columns_raises(self) -> None:
        client = _StubClient({
            "index_classify": _FakePandasFrame(
                rows=[{"foo": "bar"}], columns=["foo"],
            ),
        })
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(
                TushareIndustryPublisherError, "missing required column"
            ):
                TushareIndustryPublisher.publish(
                    artifact_path=str(Path(tmp) / "a.csv"),
                    manifest_path=str(Path(tmp) / "a.json"),
                    snapshot_at="2026-04-25",
                    client=client,
                )


# ---------------------------------------------------------------------
# End-to-end happy path with stub client
# ---------------------------------------------------------------------


class PublisherHappyPathTests(unittest.TestCase):
    """Full publish flow against a stub Tushare client.

    Verifies the artifact lands, the manifest carries Tushare-source
    provenance, and the (instrument, industry_name) rows are correctly
    converted from Tushare's dotted format to qlib's prefixed format.
    """

    def _build_stub_client(self) -> _StubClient:
        industry_df = _FakePandasFrame(rows=[
            {"index_code": "850711.SI", "industry_name": "白酒", "level": "L2"},
            {"index_code": "801010.SI", "industry_name": "银行", "level": "L2"},
            # An L1 row mixed in to confirm we filter by level.
            {"index_code": "999999.SI", "industry_name": "REJECT_ME", "level": "L1"},
        ], columns=["index_code", "industry_name", "level"])

        members_per_industry = {
            "850711.SI": _FakePandasFrame(rows=[
                {"con_code": "600519.SH", "is_new": "Y"},
                {"con_code": "000858.SZ", "is_new": "Y"},
                # Inactive member must NOT land in the artifact.
                {"con_code": "000999.SZ", "is_new": "N"},
            ], columns=["con_code", "is_new"]),
            "801010.SI": _FakePandasFrame(rows=[
                {"con_code": "601398.SH", "is_new": "Y"},
                {"con_code": "601988.SH", "is_new": "Y"},
            ], columns=["con_code", "is_new"]),
        }
        return _StubClient({
            "index_classify": industry_df,
            "index_member": members_per_industry,
        })

    def test_publish_writes_artifact_and_manifest(self) -> None:
        client = self._build_stub_client()
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "sw_l2.csv"
            manifest = Path(tmp) / "sw_l2.json"
            result = TushareIndustryPublisher.publish(
                artifact_path=str(artifact),
                manifest_path=str(manifest),
                snapshot_at="2026-04-25",
                client=client,
            )

            self.assertTrue(artifact.exists())
            self.assertTrue(manifest.exists())

            self.assertEqual(result.industries_fetched, 2)
            self.assertEqual(result.instruments_classified, 4)
            self.assertEqual(result.shenwan_src, "SW2021")
            self.assertEqual(result.level, "L2")

            # Artifact rows: instruments converted to qlib format,
            # inactive members dropped, L1 industry skipped.
            with open(artifact, encoding="utf-8") as f:
                rows = list(csv.reader(f))
            self.assertEqual(rows[0], ["instrument", "industry_code"])
            data = sorted(rows[1:])
            self.assertEqual(data, sorted([
                ["SH600519", "白酒"],
                ["SZ000858", "白酒"],
                ["SH601398", "银行"],
                ["SH601988", "银行"],
            ]))

            with open(manifest, encoding="utf-8") as f:
                meta = json.load(f)
            self.assertEqual(meta["taxonomy_name"], SW_L2_TAXONOMY_NAME)
            self.assertEqual(meta["snapshot_at"], "2026-04-25")
            # Source URI carries the Tushare query so the artifact is
            # auditable.
            self.assertIn("tushare://", meta["source_uri"])
            self.assertIn("level=L2", meta["source_uri"])

    def test_publish_rejects_zero_active_members(self) -> None:
        """Industries exist but every member is inactive (``is_new=N``)
        → resulting row count is zero, which the publisher refuses to
        write to disk."""
        empty_members = _FakePandasFrame(rows=[
            {"con_code": "000001.SZ", "is_new": "N"},
        ], columns=["con_code", "is_new"])

        client = _StubClient({
            "index_classify": _FakePandasFrame(rows=[
                {"index_code": "850711.SI", "industry_name": "白酒", "level": "L2"},
            ], columns=["index_code", "industry_name", "level"]),
            "index_member": {"850711.SI": empty_members},
        })

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(
                TushareIndustryPublisherError, "No active members"
            ):
                TushareIndustryPublisher.publish(
                    artifact_path=str(Path(tmp) / "a.csv"),
                    manifest_path=str(Path(tmp) / "a.json"),
                    snapshot_at="2026-04-25",
                    client=client,
                )

    def test_publish_rejects_duplicate_instrument_across_industries(self) -> None:
        industry_df = _FakePandasFrame(rows=[
            {"index_code": "850711.SI", "industry_name": "白酒", "level": "L2"},
            {"index_code": "801010.SI", "industry_name": "银行", "level": "L2"},
        ], columns=["index_code", "industry_name", "level"])
        duplicate_members = {
            "850711.SI": _FakePandasFrame(rows=[
                {"con_code": "600000.SH", "is_new": "Y"},
            ], columns=["con_code", "is_new"]),
            "801010.SI": _FakePandasFrame(rows=[
                {"con_code": "600000.SH", "is_new": "Y"},
            ], columns=["con_code", "is_new"]),
        }
        client = _StubClient({
            "index_classify": industry_df,
            "index_member": duplicate_members,
        })

        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "sw_l2.csv"
            manifest = Path(tmp) / "sw_l2.json"
            with self.assertRaisesRegex(
                TushareIndustryPublisherError,
                "Duplicate instrument 'SH600000'",
            ):
                TushareIndustryPublisher.publish(
                    artifact_path=str(artifact),
                    manifest_path=str(manifest),
                    snapshot_at="2026-04-25",
                    client=client,
                )
            self.assertFalse(artifact.exists())
            self.assertFalse(manifest.exists())


if __name__ == "__main__":
    unittest.main()
