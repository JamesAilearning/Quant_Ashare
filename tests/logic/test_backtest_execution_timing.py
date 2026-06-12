"""Execution-timing contract through the REAL canonical backtest path (PR-C).

Builds a tiny synthetic qlib provider in a tempdir (via the real
``QlibBinBuilder``), initializes the canonical qlib runtime against it, places
single-day signals, runs ``BacktestRunner.run`` with
``signal_to_execution_lag=1``, and asserts WHICH day positions first exist.

Why this exists (audit A1 / PR-C Step 0): the execution chain has TWO shifts
available —

- qlib's ``TopkDropoutStrategy`` internally consumes, on trade day D, the
  signal stamped D-1 (``get_step_time(trade_step, shift=1)``,
  qlib/contrib/strategy/signal_strategy.py); and
- ``BacktestRunner._apply_lag`` can additionally restamp signal dates forward.

``lag=1`` is the contract "signal from day T's close trades on day T+1".
Before PR-C both shifts applied (restamp T→T+1, then qlib consumed it on
T+2): every official backtest traded the one-day-stale signal, the
suspension/ST masks filtered one day before the true fill, and — because the
restamp shifts within the prediction's own date set — the LAST test day's
signals of every fold evaporated entirely. This module pins the fixed mapping
(lag=N ⇒ external restamp of N-1 on top of qlib's built-in one-day shift) at
the full-path level, plus the execution-day mask contract ("a ticker
suspended on T+1 must not fill even with the top day-T score").

PROCESS ISOLATION: ``qlib.init`` is process-global and cannot be undone, and
the rest of the fast suite assumes a pristine-or-mocked qlib. The probes
therefore run in a CHILD process (this file invoked with ``--emit-json``);
the pytest/unittest-visible tests only spawn it and assert on its verdict.
No real bundle, no network: everything is synthetic and lives in tempdirs.
Skipped only when qlib itself is not importable.
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
_SIGNAL_DAY = "2025-01-08"  # T (index 4)
_T_PLUS_1 = "2025-01-09"
_T_PLUS_2 = "2025-01-10"
_TICKER = "SH600000"
_BENCH = "SH000300"

_SUSPENDED_TICKER = "SH600001"
# 600001.SH is suspended (vol=0) on T+1 — its execution day — baked into the
# shared provider so both probe scenarios run under ONE qlib init.
_DEFAULT_ZERO_VOLUME = frozenset({("600001.SH", "20250109")})


def _seed_tushare(
    tushare_dir: Path, *,
    zero_volume_days: frozenset[tuple[str, str]] = _DEFAULT_ZERO_VOLUME,
) -> None:
    """Synthetic tushare dump. ``zero_volume_days`` marks (ts_code,
    trade_date) pairs as suspended (vol=0) so the microstructure mask
    flags them."""
    pd.DataFrame({
        "ts_code": ["600000.SH", "600001.SH", "000300.SH"],
        "list_date": ["20100101"] * 3,
        "list_status": ["L"] * 3,
    }).to_parquet(tushare_dir / "active_stocks.parquet", index=False)
    write_manifest(
        tushare_dir / MANIFEST_FILENAME,
        build_manifest(
            [TushareFetchResult(e, 1, 0, 0)
             for e in ("stock_basic", "daily", "adj_factor")],
            (), "20250101", "20251231",
        ),
    )
    for ts_code, base in (("600000.SH", 10.0), ("600001.SH", 20.0),
                          ("000300.SH", 4000.0)):
        pd.DataFrame({
            "ts_code": [ts_code] * len(_CAL),
            "trade_date": _CAL,
            "open": [base] * len(_CAL),
            "high": [base * 1.02] * len(_CAL),
            "low": [base * 0.98] * len(_CAL),
            "close": [base] * len(_CAL),
            "vol": [
                0.0 if (ts_code, td) in zero_volume_days else 10_000.0
                for td in _CAL
            ],
            "amount": [100_000.0] * len(_CAL),
        }).to_parquet(
            _mkdirs(tushare_dir / "daily" / "2025") / f"{ts_code}.parquet",
            index=False,
        )


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
        predictions_ref="execution_timing_probe",
        # Headroom on both sides of the synthetic calendar (which spans
        # 2025-01-02..2025-01-15): qlib's executor reads one bar beyond each
        # end of the evaluation window (shift=1 at the first step, settlement
        # at the last), and a window flush with the calendar raises
        # IndexError deep inside qlib.
        evaluation_start="2025-01-06",
        evaluation_end="2025-01-13",
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


def _single_signal_run(ticker: str):
    from src.core.backtest_runner import BacktestRunner

    predictions = pd.Series(
        [1.0],
        index=pd.MultiIndex.from_tuples(
            [(pd.Timestamp(_SIGNAL_DAY), ticker)],
            names=["datetime", "instrument"],
        ),
    )
    return BacktestRunner.run(
        request=_request(),
        predictions=predictions,
        topk=1,
        n_drop=0,
        compute_baselines=False,
    )


def _run_probes() -> dict:
    """Build the provider, init qlib (PROCESS-GLOBAL — child process only),
    run both probe backtests, return the verdicts."""
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

        timing_out = _single_signal_run(_TICKER)
        held_days = sorted(
            day for day, pos in timing_out.positions.items()
            if pos.get(_TICKER, 0.0) > 0.0
        )

        suspended_out = _single_signal_run(_SUSPENDED_TICKER)
        suspended_held = sorted(
            (day, inst)
            for day, pos in suspended_out.positions.items()
            for inst, w in pos.items()
            if inst == _SUSPENDED_TICKER and w > 0.0
        )

    return {
        "held_days": [d[:10] for d in held_days],
        "first_fill": held_days[0][:10] if held_days else None,
        "suspended_held": suspended_held,
    }


@unittest.skipUnless(_HAS_QLIB, "qlib not importable (no bundle needed — synthetic provider)")
class ExecutionTimingContractTests(unittest.TestCase):
    """lag=1 ⇒ the day-T signal fills on T+1, through the real qlib path —
    asserted on a child-process probe so the parent's qlib stays untouched."""

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
                    "execution-timing probe subprocess failed "
                    f"(rc={proc.returncode}). stderr tail:\n"
                    + "\n".join(proc.stderr.splitlines()[-40:])
                )
            cls._verdict = json.loads(result_path.read_text(encoding="utf-8"))

    def test_lag_one_fills_on_t_plus_one(self) -> None:
        first_fill = self._verdict["first_fill"]
        self.assertIsNotNone(
            first_fill,
            "the single day-T signal never produced a position at all — "
            "before PR-C the external restamp made single-day (and every "
            "fold-final-day) signals evaporate entirely.",
        )
        self.assertEqual(
            first_fill,
            _T_PLUS_1,
            "Execution-timing contract violated: the day-T signal "
            f"({_SIGNAL_DAY}) first fills on {first_fill}, expected T+1 "
            f"({_T_PLUS_1}). If this is {_T_PLUS_2}, BOTH shifts applied "
            "(external restamp + qlib's internal shift) — the audit-A1 "
            "double-lag bug. If it is the signal day itself, the built-in "
            "qlib shift was lost and the backtest looks ahead.",
        )

    def test_top_score_suspended_on_t_plus_one_never_fills(self) -> None:
        """A ticker suspended on its EXECUTION day (T+1) must not fill —
        even when its day-T score is the highest in the panel. The mask is
        the only protection: qlib itself would happily fill a vol=0 day at
        its stored close."""
        self.assertEqual(
            self._verdict["suspended_held"], [],
            "the top-scored signal filled although its execution day "
            f"(T+1 = {_T_PLUS_1}) is suspended — the microstructure mask "
            "must drop it by execution day, not stamp day: "
            f"{self._verdict['suspended_held']}",
        )


if __name__ == "__main__":
    if "--emit-json" in sys.argv:
        out_path = Path(sys.argv[sys.argv.index("--emit-json") + 1])
        out_path.write_text(json.dumps(_run_probes()), encoding="utf-8")
        sys.exit(0)
    unittest.main()
