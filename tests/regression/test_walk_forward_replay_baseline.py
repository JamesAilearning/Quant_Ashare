"""REGEN-A deterministic frozen-score replay regression test (22-fold anchor).

This is the PRIMARY walk-forward regression anchor (it replaced the single
``fold0`` anchor, which was the worst, sign-flipping, within-noise fold — see
``docs/baseline_20260616.md`` decision 2). It replays the C1 round's 22 frozen
per-fold prediction Series through the CURRENT canonical ``BacktestRunner`` and
asserts the result reproduces the committed baseline.

Because the scores are frozen and the backtest + aggregation are deterministic
(bootstrap seed 42), reproduction is exact to machine precision — so the
tolerance here is TIGHT (``REPLAY_ABS_TOL``), unlike the retrain-based
``test_walk_forward_aggregate_baseline`` whose ±5% band absorbs retrain noise.
The tolerance lives in THIS source (not in the fixture) so a tampered fixture
cannot silently widen it.

Skipped unless ALL of:
* ``RUN_E2E=1`` (needs the real qlib bundle — not CI-runnable),
* the committed frozen-scores fixture exists,
* the PIT bundle (``QUANT_PROVIDER_URI``) and namechange parquet
  (``QUANT_NAMECHANGE_PATH``) are present.
"""

from __future__ import annotations

import json
import math
import os
import unittest
from pathlib import Path

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
BASELINE_FIXTURE = FIXTURES_DIR / "walk_forward_baseline_metrics.json"
FROZEN_FIXTURE = FIXTURES_DIR / "regen_a" / "frozen_fold_scores.pkl.gz"
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Tolerance lives in SOURCE, not in the fixture (decision: a tampered fixture
# must not be able to widen its own gate). Deterministic replay reproduces to
# machine precision; 1e-6 only absorbs float-repr round-trips.
REPLAY_ABS_TOL = 1e-6

_HEADLINE_METRICS = (
    "mean_information_ratio",
    "mean_ic_1d",
    "mean_ic_5d",
    "mean_annualized_return",
    "worst_drawdown",
)

_DEFAULT_PROVIDER = "D:/qlib_data/my_cn_data_pit"
_DEFAULT_NAMECHANGE = "D:/qlib_data/tushare_raw/all_namechanges.parquet"


def _e2e_enabled() -> bool:
    return os.environ.get("RUN_E2E", "").strip() in ("1", "true", "yes")


def _provider_uri() -> str:
    return os.environ.get("QUANT_PROVIDER_URI", _DEFAULT_PROVIDER)


def _namechange_path() -> str:
    return os.environ.get("QUANT_NAMECHANGE_PATH", _DEFAULT_NAMECHANGE)


def _bundle_available() -> bool:
    return (Path(_provider_uri()) / "calendars" / "day.txt").is_file()


class WalkForwardReplayBaselineTests(unittest.TestCase):
    def setUp(self) -> None:
        if not _e2e_enabled():
            self.skipTest("RUN_E2E=1 not set — replay needs the real qlib bundle.")
        if not FROZEN_FIXTURE.is_file():
            self.skipTest(f"Frozen-scores fixture not found at {FROZEN_FIXTURE}.")
        if not BASELINE_FIXTURE.is_file():
            self.skipTest(f"Baseline fixture not found at {BASELINE_FIXTURE}.")
        if not _bundle_available():
            self.skipTest(f"PIT bundle not found at {_provider_uri()}.")
        if not Path(_namechange_path()).is_file():
            self.skipTest(f"namechange parquet not found at {_namechange_path()}.")

    def _replay(self) -> dict:
        from scripts.regen.replay_frozen_baseline import replay_frozen_baseline
        return replay_frozen_baseline(
            FROZEN_FIXTURE, _provider_uri(), _namechange_path(),
        )

    # Per-fold metrics compared by the replay test. The OpenSpec delta requires
    # the replay to reproduce EVERY committed per-fold metric, not just IR — so a
    # future IC/return/drawdown change can't leave a stale per_fold block green.
    _PER_FOLD_METRICS = (
        "ic_1d", "ic_5d", "annualized_return", "max_drawdown", "information_ratio",
    )

    @staticmethod
    def _close(a: float | None, b: float | None) -> bool:
        # null (committed, from the sanitized NaN of the failed fold 22) and NaN
        # (replay) both mean "missing" and compare equal.
        a = float("nan") if a is None else float(a)
        b = float("nan") if b is None else float(b)
        if math.isnan(a) and math.isnan(b):
            return True
        if math.isnan(a) or math.isnan(b):
            return False
        return abs(a - b) <= REPLAY_ABS_TOL

    def test_aggregate_reproduces_committed_baseline(self) -> None:
        committed = json.loads(BASELINE_FIXTURE.read_text(encoding="utf-8"))
        committed_agg = committed["aggregate_metrics"]
        result_agg = self._replay()["aggregate_metrics"]
        drifts = [
            f"{k}: replay={result_agg.get(k)!r} vs committed={committed_agg.get(k)!r}"
            for k in _HEADLINE_METRICS
            if not self._close(result_agg.get(k), committed_agg.get(k))
        ]
        if drifts:
            self.fail(
                "REGEN-A replay did NOT reproduce the committed baseline within "
                f"{REPLAY_ABS_TOL} (deterministic replay should be exact):\n  - "
                + "\n  - ".join(drifts)
                + "\n\nIf a backtest-semantics change is intentional, regenerate via "
                "scripts/regen/replay_frozen_baseline.py and re-sign the baseline."
            )

    def test_each_fold_ir_reproduces(self) -> None:
        committed = json.loads(BASELINE_FIXTURE.read_text(encoding="utf-8"))
        committed_pf = {f["fold_index"]: f for f in committed.get("per_fold", [])}
        self.assertTrue(committed_pf, "committed baseline carries no per_fold block.")
        result = self._replay()["folds"]
        drifts = []
        for fold in result:
            ref = committed_pf.get(fold.fold_index)
            if ref is None:
                continue
            for metric in self._PER_FOLD_METRICS:
                if not self._close(getattr(fold, metric), ref.get(metric)):
                    drifts.append(
                        f"fold {fold.fold_index}.{metric}: replay={getattr(fold, metric)!r} "
                        f"vs committed={ref.get(metric)!r}"
                    )
        if drifts:
            self.fail(
                "Per-fold metric drift in the deterministic replay (every fold's "
                f"{', '.join(self._PER_FOLD_METRICS)} must reproduce within "
                f"{REPLAY_ABS_TOL}):\n  - " + "\n  - ".join(drifts)
            )


if __name__ == "__main__":
    unittest.main()
