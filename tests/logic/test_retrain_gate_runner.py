"""Retrain-gate RUNNER wiring pins (PR-B', codex #391 r38).

The verdict rules live in the pure lib (tests/logic/
test_retrain_gate_lib.py). What can only be checked at the runner is
WHICH SERIES each consumer receives — the ensemble gate's Brinson
attribution must see the signal set ACTUALLY TRADED (executable N5
stamps), not the dense daily scores, or veto2/veto5 describe a
different universe than the dry run they characterise (the
walk-forward engine's own discipline, engine.py / codex #336).

Everything heavy is stubbed at the module boundary; the REAL cadence
thinning runs, so a regression back to dense predictions fails here.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

pytest.importorskip("qlib")
pd = pytest.importorskip("pandas")


class _Member:
    def __init__(self) -> None:
        self.pkl_sha256 = "aa" * 32
        self.fit_start = "2024-05-02"
        self.fit_end = "2026-05-01"


class _Calendar:
    """Stand-in for ``qlib.data.D`` (the real one is a lazy Wrapper
    with no ``calendar`` attribute before qlib is initialised)."""

    def __init__(self, days: list[pd.Timestamp]) -> None:
        self._days = days

    def calendar(self, start_time=None, end_time=None):  # noqa: ANN001
        return list(self._days)


class _Sleeves:
    sleeve_map: dict[str, str] = {}
    taxonomy_id = "csi800_sleeve_v1"


class _Row:
    def __init__(self, sector: str, weight: float, effect: float) -> None:
        self.sector = sector
        self.portfolio_weight = weight
        self.total_effect = effect


class _Attribution:
    def __init__(self) -> None:
        self.sector_attribution = (
            _Row("csi300_sleeve", 0.55, 0.06),
            _Row("csi500_sleeve", 0.45, 0.04),
        )
        self.sector_effects_sum = 0.10


class _Output:
    """Minimal CanonicalBacktestOutput stand-in."""

    def __init__(self, dates: list[pd.Timestamp]) -> None:
        self.positions = {
            d.strftime("%Y-%m-%d"): {"SH600000": 0.5, "SZ000001": 0.5}
            for d in dates
        }
        self.return_series = {"return": {}, "bench": {}, "cost": {}}
        self.risk_analysis: dict[str, dict[str, float]] = {}


def _dense_predictions(days: list[pd.Timestamp]) -> pd.Series:
    idx = pd.MultiIndex.from_product(
        [days, ["SH600000", "SZ000001", "SZ300750"]])
    return pd.Series(range(len(idx)), index=idx, dtype="float64")


def test_attribution_receives_traded_stamps_not_dense_scores() -> None:
    import scripts.retrain_gate as rg

    # Four ISO weeks of weekdays: dense = 20 stamp days, the N5
    # iso_week schedule keeps one per week.
    days = [d for d in pd.date_range("2026-05-04", periods=28, freq="D")
            if d.weekday() < 5]
    dense = _dense_predictions(days)
    seen: dict[str, pd.Series] = {}

    def _record(**kwargs):  # noqa: ANN003
        seen["predictions"] = kwargs["predictions"]
        return _Attribution()

    args = rg.argparse.Namespace(
        scope="ensemble", profile="csi800_n5",
        provider="Z:/provider", namechange="Z:/namechange",
        handler="Alpha158", manifest="Z:/candidate.json",
        window_start="2026-05-04", window_end="2026-05-29",
        member_pkl=None, member_meta=None, fit_start=None, fit_end=None,
        valid_start=None, valid_end=None, out="Z:/out.json")
    profile = rg.resolve_profile("csi800_n5")

    with patch.object(rg, "load_ensemble_manifest",
                      return_value=((_Member(),), "cd" * 32)), \
            patch.object(rg, "load_member_models", return_value=[]), \
            patch.object(rg, "_scoring_dataset", return_value=object()), \
            patch.object(rg, "ensemble_predict", return_value=dense), \
            patch.object(rg, "resolve_sleeve_map",
                         return_value=_Sleeves()), \
            patch.object(rg, "_anchor_turnover_daily_mean",
                         return_value=(0.03, {"rev": "f" * 40})), \
            patch.object(rg.BacktestRunner, "run",
                         return_value=_Output(days)), \
            patch.object(rg.PerformanceAttribution, "analyze",
                         side_effect=_record), \
            patch("qlib.data.D", _Calendar(days)):
        artifact = rg._ensemble_scope(args, profile)

    passed = seen["predictions"]
    dense_days = dense.index.get_level_values(0).nunique()
    traded_days = passed.index.get_level_values(0).nunique()
    # The regression this pins: attribution must NOT see every stamp.
    assert traded_days < dense_days
    # ...and must see EXACTLY the executable N5 stamp set the
    # degeneracy gate and the dry run use.
    expected = rg.BacktestRunner._thin_predictions(
        dense,
        cadence_days=profile["rebalance_cadence_days"],
        phase=profile["rebalance_phase"],
        anchor=profile["rebalance_anchor"],
        trading_calendar=list(days),
    )
    assert list(passed.index) == list(expected.index)
    assert artifact["scope"] == "ensemble"
