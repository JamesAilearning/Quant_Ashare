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
# shared provider so all probe scenarios run under ONE qlib init.
_DEFAULT_ZERO_VOLUME = frozenset({("600001.SH", "20250109")})

# PR-D price-limit probes. Both limit days are deliberately NOT one-price
# (high != low), so the microstructure mask does not fire and the qlib
# price-limit check is the ONLY protection:
# - 600002.SH closes +10% on T+1 (its would-be fill day)  → buy must block.
# - 600003.SH closes -10% on T+1 (its would-be sell day)  → sell must block.
_LIMIT_UP_TICKER = "SH600002"
_LIMIT_DOWN_TICKER = "SH600003"
# 600004.SH has NO bar at all on T (2025-01-08) — a real suspension gap in
# the bins — and resumes on T+1 at +10% vs its last close. Its T+1 move is
# UNVERIFIABLE through Ref($close,1) (NaN), so the Not-form limit
# expressions must block the fill conservatively.
_RESUMED_TICKER = "SH600004"
_FLAT = [10.0] * len(_CAL)
_CLOSES: dict[str, list[float]] = {
    "600000.SH": _FLAT,
    "600001.SH": [20.0] * len(_CAL),
    "000300.SH": [4000.0] * len(_CAL),
    # index 5 == 2025-01-09 (T+1).
    "600002.SH": [10.0] * 5 + [11.0] * 5,
    "600003.SH": [10.0] * 5 + [9.0] * 5,
    "600004.SH": [10.0] * 5 + [11.0] * 5,  # the 01-08 row is dropped below
}
_MISSING_ROWS = frozenset({("600004.SH", "20250108")})


def _seed_tushare(
    tushare_dir: Path, *,
    zero_volume_days: frozenset[tuple[str, str]] = _DEFAULT_ZERO_VOLUME,
) -> None:
    """Synthetic tushare dump. ``zero_volume_days`` marks (ts_code,
    trade_date) pairs as suspended (vol=0, bar present) so the
    microstructure mask flags them; ``_MISSING_ROWS`` drops the bar
    entirely (NaN in the bins — the suspension-gap shape). OHLC derives
    from the per-ticker close series with high != low on EVERY day, so
    one-price-lock masking never fires and limit-day behavior is qlib's
    to enforce."""
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
        frame = pd.DataFrame({
            "ts_code": [ts_code] * len(_CAL),
            "trade_date": _CAL,
            "open": [c * 0.99 for c in closes],
            "high": [c * 1.005 for c in closes],
            "low": [c * 0.97 for c in closes],
            "close": closes,
            "vol": [
                0.0 if (ts_code, td) in zero_volume_days else 10_000.0
                for td in _CAL
            ],
            "amount": [100_000.0] * len(_CAL),
        })
        frame = frame[~frame["trade_date"].map(
            lambda td, _t=ts_code: (_t, td) in _MISSING_ROWS,
        )]
        frame.to_parquet(
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


def _single_signal_run(ticker: str, stamp: str = _SIGNAL_DAY):
    from src.core.backtest_runner import BacktestRunner

    predictions = pd.Series(
        [1.0],
        index=pd.MultiIndex.from_tuples(
            [(pd.Timestamp(stamp), ticker)],
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


def _rotation_run():
    """Two-signal rotation for the limit-down SELL probe: 600003 enters the
    book off the 01-06 signal (fills 01-07 alongside 600000). The 01-08
    signal no longer scores 600003 (its rows are 600001 — which the
    execution-day mask drops, 600001 being suspended on 01-09 — and
    600000), so on 01-09 the strategy rotates the unscored 600003 out and
    tries to SELL it — on its -10% limit-down day."""
    from src.core.backtest_runner import BacktestRunner

    predictions = pd.Series(
        [9.0, 8.0, 9.0, 8.0],
        index=pd.MultiIndex.from_tuples(
            [
                (pd.Timestamp("2025-01-06"), _LIMIT_DOWN_TICKER),
                (pd.Timestamp("2025-01-06"), _TICKER),
                (pd.Timestamp("2025-01-08"), _SUSPENDED_TICKER),
                (pd.Timestamp("2025-01-08"), _TICKER),
            ],
            names=["datetime", "instrument"],
        ),
    )
    return BacktestRunner.run(
        request=_request(),
        predictions=predictions,
        topk=2,
        n_drop=1,
        compute_baselines=False,
    )


def _run_probes() -> dict:
    """Build the provider, init qlib (PROCESS-GLOBAL — child process only),
    run the probe backtests, return the verdicts."""
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

        # The suspended name's ONLY signal is removed by the execution-day
        # mask, so the post-mask universe is empty and the runner refuses to
        # emit zero-position official metrics (PR-D fail-loud) — that
        # refusal is the strongest possible proof the name cannot fill. A
        # completed run with no position is equally acceptable evidence.
        from src.core.backtest_runner import BacktestRunnerError
        try:
            suspended_out = _single_signal_run(_SUSPENDED_TICKER)
            suspended_held = sorted(
                (day, inst)
                for day, pos in suspended_out.positions.items()
                for inst, w in pos.items()
                if inst == _SUSPENDED_TICKER and w > 0.0
            )
        except BacktestRunnerError as exc:
            if "universe is empty" not in str(exc):
                raise
            suspended_held = []

        # PR-D limit probes (audit A2). Buy side: top score, but T+1 closes
        # +10% — the buy must be blocked at the limit, never filled.
        limit_up_out = _single_signal_run(_LIMIT_UP_TICKER)
        limit_up_held = sorted(
            day[:10] for day, pos in limit_up_out.positions.items()
            if pos.get(_LIMIT_UP_TICKER, 0.0) > 0.0
        )
        # Control for probe vacuity (self-review P2): the SAME limit-up
        # ticker, signalled one day later, fills on 01-10 at a 0% move —
        # proving 600002 is buyable when not at limit, so the block above
        # is attributable to the limit and nothing else.
        control_out = _single_signal_run(_LIMIT_UP_TICKER, stamp=_T_PLUS_1)
        control_held = sorted(
            day[:10] for day, pos in control_out.positions.items()
            if pos.get(_LIMIT_UP_TICKER, 0.0) > 0.0
        )

        # Sell side: held name rotated out on its -10% day — the sell must
        # be blocked, so it is STILL held on (and after) the limit-down day.
        rotation_out = _rotation_run()
        limit_down_held = sorted(
            day[:10] for day, pos in rotation_out.positions.items()
            if pos.get(_LIMIT_DOWN_TICKER, 0.0) > 0.0
        )

        # Resumption gap (self-review P2 / Not-form): 600004 has NO bar on
        # 01-08 and resumes 01-09 at +10% vs its last close. Its 01-09 move
        # is unverifiable through Ref($close,1) (NaN) — the Not-form
        # expressions must block the fill; the bare `>` form would permit
        # it (numpy NaN comparisons are False).
        resumed_out = _single_signal_run(_RESUMED_TICKER)
        resumed_held = sorted(
            day[:10] for day, pos in resumed_out.positions.items()
            if pos.get(_RESUMED_TICKER, 0.0) > 0.0
        )

    return {
        "held_days": [d[:10] for d in held_days],
        "first_fill": held_days[0][:10] if held_days else None,
        "suspended_held": suspended_held,
        "limit_up_held": limit_up_held,
        "limit_up_control_held": control_held,
        "limit_down_held": limit_down_held,
        "resumed_gap_held": resumed_held,
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

    def test_limit_up_buy_is_blocked(self) -> None:
        """PR-D (audit A2): a +10% close on the fill day blocks the BUY at
        the price limit. The limit day is deliberately NOT one-price, so the
        microstructure mask does not fire — qlib's expression-mode
        limit_threshold is the only protection. Under the old float mode the
        PIT bundle's missing $change field disabled the check entirely and
        this signal filled AT the limit-up close."""
        self.assertEqual(
            self._verdict["limit_up_held"], [],
            "the top-scored signal filled on its +10% limit-up day "
            f"({_T_PLUS_1}) — price-limit enforcement is not active: "
            f"{self._verdict['limit_up_held']}",
        )
        # Vacuity control: the SAME ticker, signalled one day later, fills
        # on 01-10 at a 0% move — so the empty book above is attributable
        # to the limit, not to the name being unbuyable for another reason.
        control = self._verdict["limit_up_control_held"]
        self.assertTrue(
            control and control[0] == "2025-01-10",
            "control fill missing — the limit-up probe would be vacuous: "
            f"{control}",
        )

    def test_resumption_gap_unverifiable_move_blocked(self) -> None:
        """PR-D self-review: a ticker with NO bar on T that resumes on T+1
        at +10% has an UNVERIFIABLE close move (Ref($close,1) is NaN). The
        Not-form limit expressions block it conservatively; the bare `>`
        form would silently permit the fill (numpy NaN comparisons are
        False) — the same liberal failure class as the dead float mode."""
        self.assertEqual(
            self._verdict["resumed_gap_held"], [],
            "the resumption-gap signal filled although its move is "
            f"unverifiable: {self._verdict['resumed_gap_held']}",
        )

    def test_limit_down_sell_is_blocked(self) -> None:
        """PR-D (audit A2): a held name rotated out on its -10% day cannot
        be sold at the limit — it must STILL be in the book on the limit-down
        day (and after, since no later rebalance signal exists)."""
        held = self._verdict["limit_down_held"]
        self.assertIn(
            _T_PLUS_1, held,
            "the limit-down name was sold ON its -10% day — sell-side "
            f"price-limit enforcement is not active. held_days={held}",
        )
        self.assertTrue(
            held and held[-1] > _T_PLUS_1,
            "the blocked sell should leave the name held beyond the "
            f"limit-down day (no later rebalance exists). held_days={held}",
        )


if __name__ == "__main__":
    if "--emit-json" in sys.argv:
        out_path = Path(sys.argv[sys.argv.index("--emit-json") + 1])
        out_path.write_text(json.dumps(_run_probes()), encoding="utf-8")
        sys.exit(0)
    unittest.main()
