"""Rebalance-cadence CONTRACT through the REAL qlib backtest path (阶段7,
operator condition 1 — BLOCKING acceptance).

The Step-0 recon proved qlib holds the portfolio on no-signal days at the
SOURCE level (strategy None-branch → ``TradeDecisionWO([], self)``;
``resam_ts_data`` slices strictly in-window, no stale backfill). Source
evidence proves "qlib does this today", not "forever" — this module pins the
behavior with a REAL backtest so a qlib upgrade that flips the empty-window
semantics turns red HERE first. Three operator-mandated assertions, on
no-signal days:

  (a) ZERO orders     — cost == 0 (trades are the only cost source; the two
                        rebalance fill days carry cost > 0 as the positive
                        control);
  (b) positions held  — the held instrument SET is unchanged day-over-day
                        (positions serialize as VALUE weights, which drift
                        with prices — that drift is exactly assertion (c)'s
                        substance, so the set is the no-trade invariant);
  (c) market-value accrual — the portfolio return is nonzero on a no-fill
                        day (every synthetic close moves daily by
                        construction).

PROCESS ISOLATION mirrors tests/logic/test_backtest_execution_timing.py:
``qlib.init`` is process-global, so the probe runs in a CHILD process (this
file invoked with ``--emit-json``); the unittest methods only assert on the
emitted verdict. Everything is synthetic and tempdir-local; skipped only
when qlib is not importable.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.tushare.fetch_manifest import (  # noqa: E402
    MANIFEST_FILENAME,
    build_manifest,
    write_manifest,
)
from src.data.tushare.fetch_types import TushareFetchResult  # noqa: E402

_HAS_QLIB = importlib.util.find_spec("qlib") is not None

# Ten consecutive CN trading weekdays (Jan 2025; 4-5 and 11-12 are weekends).
_CAL = [
    "20250102", "20250103", "20250106", "20250107", "20250108",
    "20250109", "20250110", "20250113", "20250114", "20250115",
]
_BENCH = "SH000300"

# Every close MOVES every day (distinct gentle slopes) so a held portfolio
# accrues nonzero market-value returns on no-trade days — assertion (c).
_CLOSES: dict[str, list[float]] = {
    "600010.SH": [10.0 + 0.10 * i for i in range(len(_CAL))],   # A
    "600011.SH": [20.0 + 0.15 * i for i in range(len(_CAL))],   # B
    "600012.SH": [30.0 + 0.20 * i for i in range(len(_CAL))],   # C
    "000300.SH": [4000.0 + 1.0 * i for i in range(len(_CAL))],
}

# Signal stamps: daily scores on the first 8 trading days. A/B lead until
# the stamp of 2025-01-09; from there C overtakes A. With cadence N=5 /
# phase=0 the THINNED stamps are exactly {2025-01-02, 2025-01-09}:
#   * 2025-01-02 (kept):  top2 = A, B  -> entry fills on 2025-01-03
#   * 2025-01-09 (kept):  top2 = C, B  -> rotate A->C on 2025-01-10
#   * every other stamp is thinned away -> qlib must HOLD those days
_STAMPS = [
    "2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07",
    "2025-01-08", "2025-01-09", "2025-01-10", "2025-01-13",
]
_FILL_DAYS = {"2025-01-03", "2025-01-10"}
_HOLD_AB_DAYS = ["2025-01-06", "2025-01-07", "2025-01-08", "2025-01-09"]


def _scores_for(stamp: str) -> dict[str, float]:
    if stamp < "2025-01-09":
        return {"SH600010": 9.0, "SH600011": 8.0, "SH600012": 1.0}
    return {"SH600010": 1.0, "SH600011": 8.0, "SH600012": 9.0}


def _mkdirs(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def _empty_registry(path: Path) -> Path:
    pd.DataFrame({
        "ticker": pd.Series([], dtype=str),
        "list_date": pd.Series([], dtype="datetime64[ns]"),
        "delist_date": pd.Series([], dtype="datetime64[ns]"),
        "last_company_name": pd.Series([], dtype=str),
        "delist_reason": pd.Series([], dtype=str),
    }).to_parquet(path, index=False)
    return path


def _seed_tushare(tushare_dir: Path) -> None:
    """Synthetic dump: 3 always-tradable movers + the benchmark. High != low
    every day so the one-price mask never fires; volume always positive so
    the suspension mask never fires — the ONLY variable is the signal
    cadence."""
    pd.DataFrame({
        "ts_code": sorted(_CLOSES),
        "list_date": ["20100101"] * len(_CLOSES),
        "list_status": ["L"] * len(_CLOSES),
    }).to_parquet(tushare_dir / "active_stocks.parquet", index=False)
    write_manifest(
        tushare_dir / MANIFEST_FILENAME,
        build_manifest(
            [TushareFetchResult(e, 1, 0, 0)
             for e in ("stock_basic", "daily", "adj_factor")],
            (), "20250101", "20251231",
        ),
    )
    for ts_code, closes in _CLOSES.items():
        pd.DataFrame({
            "ts_code": [ts_code] * len(_CAL),
            "trade_date": _CAL,
            "open": [c * 0.99 for c in closes],
            "high": [c * 1.005 for c in closes],
            "low": [c * 0.97 for c in closes],
            "close": closes,
            "vol": [10_000.0] * len(_CAL),
            "amount": [100_000.0] * len(_CAL),
        }).to_parquet(
            _mkdirs(tushare_dir / "daily" / "2025") / f"{ts_code}.parquet",
            index=False,
        )


def _request():
    from src.core.canonical_backtest_contract import (
        ADJUST_MODE_PRE,
        CN_STAMP_TAX_SCHEDULE_DEFAULT,
        EXECUTION_PRICE_CLOSE,
        CanonicalAccountConfig,
        CanonicalBacktestInput,
        CanonicalExchangeConfig,
        CanonicalExchangeCostModel,
    )

    return CanonicalBacktestInput(
        predictions_ref="rebalance_cadence_probe",
        # Headroom on both sides of the seeded calendar (2025-01-02..15):
        # qlib's executor reads one bar beyond each end of the window.
        evaluation_start="2025-01-03",
        evaluation_end="2025-01-14",
        account_config=CanonicalAccountConfig(init_cash=1_000_000),
        exchange_config=CanonicalExchangeConfig(
            freq="day",
            execution_price_kind=EXECUTION_PRICE_CLOSE,
            cost_model=CanonicalExchangeCostModel(
                commission_rate=0.0005,
                stamp_tax_schedule=CN_STAMP_TAX_SCHEDULE_DEFAULT,
                slippage_bps=5.0,
                min_cost=5.0,
            ),
        ),
        adjust_mode=ADJUST_MODE_PRE,
        signal_to_execution_lag=1,
        benchmark_code=_BENCH,
    )


def _run_probe() -> dict:
    """Child-process body: build the provider, init qlib, run ONE thinned
    backtest, emit the verdict inputs."""
    from src.core.backtest_runner import BacktestRunner
    from src.core.canonical_backtest_contract import ADJUST_MODE_PRE
    from src.core.qlib_runtime import (
        QlibRuntimeConfig,
        _reset_canonical_qlib_runtime_for_tests,
        init_qlib_canonical,
    )
    from src.data.pit.qlib_bin_builder import QlibBinBuilder

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        tushare_dir = _mkdirs(root / "tushare")
        _seed_tushare(tushare_dir)
        provider = root / "provider"
        QlibBinBuilder(
            tushare_dir=tushare_dir,
            delisted_registry_path=_empty_registry(root / "registry.parquet"),
            output_dir=provider,
        ).build()
        _reset_canonical_qlib_runtime_for_tests()
        init_qlib_canonical(QlibRuntimeConfig(
            provider_uri=str(provider),
            region="cn",
            data_adjust_mode=ADJUST_MODE_PRE,
        ))
        # Single-process qlib reads: the multiprocessing pool can hang on
        # Windows handle duplication under non-interactive shells (see
        # memory feedback-qlib-kernels-windows) — and determinism is free.
        from qlib.config import C
        C["kernels"] = 1

        rows: list[tuple[tuple[pd.Timestamp, str], float]] = []
        for stamp in _STAMPS:
            for inst, score in _scores_for(stamp).items():
                rows.append(((pd.Timestamp(stamp), inst), score))
        predictions = pd.Series(
            [v for _, v in rows],
            index=pd.MultiIndex.from_tuples(
                [k for k, _ in rows], names=["datetime", "instrument"],
            ),
        )

        output = BacktestRunner.run(
            request=_request(),
            predictions=predictions,
            topk=2,
            n_drop=1,
            compute_baselines=False,
            rebalance_cadence_days=5,
            rebalance_phase=0,
            rebalance_anchor="fold_phase",
        )
        held_by_day = {
            day: sorted(inst for inst, w in pos.items() if w > 0.0)
            for day, pos in output.positions.items()
        }
        return {
            "cost": dict(output.return_series["cost"]),
            "ret": dict(output.return_series["return"]),
            "held": held_by_day,
        }


@unittest.skipUnless(_HAS_QLIB, "qlib not importable")
class RebalanceCadenceContractTests(unittest.TestCase):
    """No-signal days hold — asserted through the real qlib path on a
    child-process probe (the parent's qlib stays untouched)."""

    _verdict: dict

    @classmethod
    def setUpClass(cls) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result_path = Path(tmp) / "verdict.json"
            proc = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()),
                 "--emit-json", str(result_path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300,
                cwd=str(PROJECT_ROOT),
            )
            if proc.returncode != 0 or not result_path.exists():
                raise AssertionError(
                    "rebalance-cadence probe subprocess failed "
                    f"(rc={proc.returncode}). stderr tail:\n"
                    + "\n".join(proc.stderr.splitlines()[-40:])
                )
            cls._verdict = json.loads(result_path.read_text(encoding="utf-8"))

    def _cost(self, day: str) -> float:
        return float(self._verdict["cost"].get(day, 0.0))

    def test_zero_orders_on_no_signal_days(self) -> None:
        # (a) trades are the only cost source: the two rebalance fills carry
        # cost; EVERY other in-window day is cost-free — including
        # 2025-01-14, whose stamp (01-13) was thinned away and would have
        # traded daily.
        for day in _FILL_DAYS:
            self.assertGreater(
                self._cost(day), 0.0,
                f"expected a REAL fill on {day} (positive control)",
            )
        for day in [*_HOLD_AB_DAYS, "2025-01-13", "2025-01-14"]:
            self.assertEqual(
                self._cost(day), 0.0,
                f"orders were placed on the no-signal day {day} — the "
                "empty-window hold contract is broken (qlib semantics "
                "changed?)",
            )

    def test_positions_held_between_rebalances(self) -> None:
        # (b) the held SET is frozen between the two fills: A+B throughout
        # the thinned stretch, then B+C after the 01-10 rotation.
        held = self._verdict["held"]
        for day in _HOLD_AB_DAYS:
            self.assertEqual(
                held.get(day), ["SH600010", "SH600011"],
                f"held set changed on no-signal day {day}: {held.get(day)}",
            )
        self.assertEqual(held.get("2025-01-10"), ["SH600011", "SH600012"])
        for day in ("2025-01-13", "2025-01-14"):
            self.assertEqual(held.get(day), ["SH600011", "SH600012"])

    def test_market_value_accrues_on_no_signal_days(self) -> None:
        # (c) holding is not freezing: every close moves daily by
        # construction, so the held portfolio must show nonzero returns on
        # no-trade days.
        moving = [
            day for day in _HOLD_AB_DAYS
            if abs(float(self._verdict["ret"].get(day, 0.0))) > 0.0
        ]
        self.assertTrue(
            moving,
            "no market-value accrual on any no-signal hold day — positions "
            f"appear frozen in value too: ret={self._verdict['ret']}",
        )


if __name__ == "__main__":
    if "--emit-json" in sys.argv:
        out_path = Path(sys.argv[sys.argv.index("--emit-json") + 1])
        out_path.write_text(json.dumps(_run_probe()), encoding="utf-8")
        sys.exit(0)
    unittest.main()
