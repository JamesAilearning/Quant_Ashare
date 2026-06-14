"""CLI tests for ``scripts/data_pipeline/07_ingest_benchmark.py`` (PR-E).

The tushare client is faked (no network); a temp bundle skeleton receives
the bins. Asserts the index-map default, the price+total-return ingest, and
the fail-loud empty-frame path.
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_CAL = [
    "2025-01-02", "2025-01-03", "2025-01-06",
    "2025-01-07", "2025-01-08", "2025-01-09",
]


def _load_cli():
    path = PROJECT_ROOT / "scripts" / "data_pipeline" / "07_ingest_benchmark.py"
    spec = importlib.util.spec_from_file_location("_ingest_benchmark_under_test", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _bundle(tmp: Path) -> Path:
    (tmp / "calendars").mkdir(parents=True)
    (tmp / "calendars" / "day.txt").write_text("\n".join(_CAL) + "\n", encoding="utf-8")
    (tmp / "instruments").mkdir(parents=True)
    (tmp / "instruments" / "all.txt").write_text(
        "SH600000\t2018-01-02\t2025-12-31\n", encoding="utf-8",
    )
    return tmp


def _yyyymmdd() -> list[str]:
    return [d.replace("-", "") for d in _CAL]


class IngestBenchmarkCliTests(unittest.TestCase):
    def setUp(self) -> None:
        # ``main()`` calls ``setup_logging`` which attaches a root INFO
        # handler that would persist into the shared test process and make
        # other modules' lazy log-format errors surface out of order. The
        # CLI is re-imported fresh per ``_load_cli()`` and binds
        # ``setup_logging`` by name at import, so patch it at the SOURCE
        # before the test body loads the CLI — the fresh import then binds
        # the no-op.
        patcher = patch("src.core.logger.setup_logging", lambda *a, **k: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _fake_client(self, frames: dict[str, pd.DataFrame]):
        def call(api, **params):
            assert api == "index_daily", api
            return frames.get(params["ts_code"])
        client = MagicMock()
        client.call = MagicMock(side_effect=call)
        return client

    def test_default_map_ingests_price_and_total_return(self) -> None:
        mod = _load_cli()
        price = pd.DataFrame({
            "ts_code": ["000300.SH"] * 6, "trade_date": _yyyymmdd(),
            "open": [1.0] * 6, "high": [1.1] * 6, "low": [0.9] * 6,
            "close": [1.0, 1.1, 1.2, 1.3, 1.4, 1.5], "vol": [10.0] * 6,
        })
        # Total-return: close only (OHLC/vol None) — the H00300 shape.
        tr = pd.DataFrame({
            "ts_code": ["H00300.CSI"] * 6, "trade_date": _yyyymmdd(),
            "open": [None] * 6, "high": [None] * 6, "low": [None] * 6,
            "close": [2.0, 2.1, 2.2, 2.3, 2.4, 2.5], "vol": [None] * 6,
        })
        client = self._fake_client({"000300.SH": price, "H00300.CSI": tr})
        with tempfile.TemporaryDirectory() as t:
            prov = _bundle(Path(t))
            with patch.object(mod.TushareClient, "from_environment", return_value=client):
                rc = mod.main(["--provider-dir", str(prov),
                               "--start-date", "20250101", "--end-date", "20251231"])
            self.assertEqual(rc, 0)
            # Benchmarks land in benchmark.txt, never the training all.txt.
            bench_txt = (prov / "instruments" / "benchmark.txt").read_text().splitlines()
            self.assertTrue(any(ln.startswith("SH000300\t") for ln in bench_txt))
            self.assertTrue(any(ln.startswith("SH000300TR\t") for ln in bench_txt))
            all_txt = (prov / "instruments" / "all.txt").read_text()
            self.assertNotIn("SH000300", all_txt)
            tr_close = np.fromfile(
                prov / "features" / "sh000300tr" / "close.day.bin", dtype="<f4",
            )[1:]
            np.testing.assert_allclose(tr_close, [2.0, 2.1, 2.2, 2.3, 2.4, 2.5], rtol=1e-5)

    def test_empty_frame_fails_loud(self) -> None:
        mod = _load_cli()
        client = self._fake_client({"000300.SH": pd.DataFrame()})
        with tempfile.TemporaryDirectory() as t:
            prov = _bundle(Path(t))
            with patch.object(mod.TushareClient, "from_environment", return_value=client):
                rc = mod.main(["--provider-dir", str(prov),
                               "--index-map", "000300.SH:SH000300"])
            self.assertEqual(rc, 1)

    def test_best_effort_index_failure_skips_not_fails(self) -> None:
        # P1 (self-review): a best-effort (total-return) index permission /
        # fetch failure must DOWNGRADE to skip+warn, not block the swap —
        # the mandatory price index still ingests and the run returns 0.
        mod = _load_cli()
        price = pd.DataFrame({
            "trade_date": _yyyymmdd(), "close": [1.0, 1.1, 1.2, 1.3, 1.4, 1.5],
            "open": [1.0] * 6, "high": [1.1] * 6, "low": [0.9] * 6, "vol": [1.0] * 6,
        })

        def call(api, **params):
            if params["ts_code"] == "H00300.CSI":
                raise mod.TushareClientError("权限不足: no index permission")
            return price
        client = MagicMock()
        client.call = MagicMock(side_effect=call)
        with tempfile.TemporaryDirectory() as t:
            prov = _bundle(Path(t))
            with patch.object(mod.TushareClient, "from_environment", return_value=client):
                rc = mod.main(["--provider-dir", str(prov)])  # default best-effort H00300
            self.assertEqual(rc, 0)
            bench_txt = (prov / "instruments" / "benchmark.txt").read_text()
            self.assertIn("SH000300\t", bench_txt)        # price ingested
            self.assertNotIn("SH000300TR\t", bench_txt)   # TR skipped, not fatal

    def test_transform_failure_is_fatal_even_for_best_effort_index(self) -> None:
        # codex P2 on #243: best-effort downgrades FETCH failures only. A
        # successful fetch whose TRANSFORM fails (here: a duplicate date in
        # the best-effort total-return index) must FAIL the run, not skip —
        # a malformed source must not silently ship a price-only benchmark.
        mod = _load_cli()
        price = pd.DataFrame({
            "trade_date": _yyyymmdd(), "close": [1.0, 1.1, 1.2, 1.3, 1.4, 1.5],
            "open": [1.0] * 6, "high": [1.1] * 6, "low": [0.9] * 6, "vol": [1.0] * 6,
        })
        bad_tr = pd.DataFrame({  # duplicate trade_date -> transform error
            "trade_date": ["20250102", "20250102"], "close": [2.0, 2.1],
        })

        def call(api, **params):
            return bad_tr if params["ts_code"] == "H00300.CSI" else price
        client = MagicMock()
        client.call = MagicMock(side_effect=call)
        with tempfile.TemporaryDirectory() as t:
            prov = _bundle(Path(t))
            with patch.object(mod.TushareClient, "from_environment", return_value=client):
                rc = mod.main(["--provider-dir", str(prov)])  # H00300 is default best-effort
            self.assertEqual(rc, 1)  # transform failure is fatal despite best-effort

    def test_all_indices_failing_returns_1(self) -> None:
        # Even all-best-effort: zero ingested is a loud failure, not a no-op.
        mod = _load_cli()
        client = MagicMock()
        client.call = MagicMock(side_effect=mod.TushareClientError("down"))
        with tempfile.TemporaryDirectory() as t:
            prov = _bundle(Path(t))
            with patch.object(mod.TushareClient, "from_environment", return_value=client):
                rc = mod.main(["--provider-dir", str(prov),
                               "--best-effort", "000300.SH,H00300.CSI"])
            self.assertEqual(rc, 1)

    def test_index_map_override_parsed(self) -> None:
        mod = _load_cli()
        self.assertEqual(
            mod._parse_index_map("000300.SH:SH000300, H00300.CSI:SH000300TR"),
            (("000300.SH", "SH000300"), ("H00300.CSI", "SH000300TR")),
        )
        with self.assertRaises(ValueError):
            mod._parse_index_map("no-colon-here")
        # codex P2 on #243: an empty side is a config error, not a blank
        # instrument written under features/ + benchmark.txt.
        for bad in ("000300.SH:", ":SH000300", " : "):
            with self.assertRaisesRegex(ValueError, "empty|required|TUSHARE_CODE:QLIB_NAME"):
                mod._parse_index_map(bad)


if __name__ == "__main__":
    unittest.main()
