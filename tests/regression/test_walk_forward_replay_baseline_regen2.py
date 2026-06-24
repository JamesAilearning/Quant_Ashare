"""REGEN-2 total-return deterministic frozen-score replay — CI-REAL (no bundle gate).

This is the REGEN-2 sibling of ``test_walk_forward_replay_baseline`` (the REGEN-A
price-index anchor, which stays RUN_E2E-gated on the full production bundle). The
``v2-canonical-backtest-contract`` OpenSpec spec requires the committed
walk-forward regression baseline to be reproducible by a DETERMINISTIC
frozen-score replay to machine precision. REGEN-2 is a fresh GPU retrain, so its
replay anchor is the frozen post-ensemble scores (``freeze_regen2_scores.py``).

Unlike the REGEN-A test, this runs **CI-real**: NOT RUN_E2E-gated, no full bundle.
It replays the 23 frozen REGEN-2 folds at the TR benchmark against a COMMITTED
minimal qlib mini-bundle — shipped as a single checksummed gzip tarball
(``fixtures/regen2_minibundle.tar.gz``, close/high/low/volume bins for exactly the
prediction universe + the two benchmarks, full-calendar, byte-identical to
production) — and asserts the result reproduces the committed REGEN-2 baseline JSON
to a TIGHT in-source tolerance. This closes the "the regression test exists but is
skipped in CI" gap.

Determinism: frozen scores + bootstrap seed 42 + the same bins qlib reads in
production => reproduction is byte-identity ON THE CANONICAL DEPENDENCY STACK
(observed max drift ~1e-14). The tolerance lives in THIS source (not the fixture)
so a tampered fixture cannot widen its own gate; the tarball is checksum-verified
before use so tampered reference data fails loudly.

DEPENDENCY-STACK CAVEAT (NOT a cross-OS one) — reproduction depends on the
dependency stack, not the OS. This runs on ONE CI leg (ubuntu-3.12; see
.github/workflows/test.yml), and CI runs the project's canonical pin on every leg
(pyproject: numpy<2, scipy<1.14, pandas<2.3); Linux-numpy<2 and Windows-numpy<2
agree on fold-0 to ~1e-15. fold-0's frozen scores are DEGENERATE — ~39 discrete
value-buckets over 300 stocks (every other fold has 300 continuous unique scores;
pre-existing in the lineage, REGEN-A's fold-0 too — filed to phase-6), so the
topk=50 cutoff lands inside a tie block and the selected names depend on numpy's
SORT tie-break, which differs across numpy MAJORS. The committed baseline is
generated ON the canonical pin (a gen-env==canonical assertion in
replay_frozen_baseline_regen2 fails generation loud off-pin), so CI reproduces it.
A drift past 1e-6 means the dependency stack moved — investigate, do NOT loosen.
(The earlier "Windows is the correct side / qlib cross-OS bug" framing was
DISPROVEN: both CI runners agreed; the split was an off-pin numpy 2.4.4 dev box.)

The replay (23 backtests) runs ONCE in ``setUpClass`` and both test methods read
the cached result. Skipped ONLY if qlib is unavailable or a committed fixture is
missing — neither holds on the canonical CI leg, so this DOES run there for real.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import shutil
import tarfile
import tempfile
import unittest
from pathlib import Path
from typing import Any

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
FROZEN_FIXTURE = FIXTURES_DIR / "regen2" / "frozen_fold_scores.pkl.gz"
BASELINE_FIXTURE = FIXTURES_DIR / "regen2" / "walk_forward_baseline_metrics.json"
TARBALL = FIXTURES_DIR / "regen2_minibundle.tar.gz"
TARBALL_SHA256 = FIXTURES_DIR / "regen2_minibundle.tar.gz.sha256"
_ARCROOT = "regen2_minibundle"  # the dir name inside the tarball

# Tolerance lives in SOURCE (a tampered fixture must not widen its own gate).
# On the canonical dependency stack the replay drifts ~1e-14; 1e-6 only absorbs the
# float-repr round-trip through the committed JSON + same-stack float jitter. A drift
# past 1e-6 means the dependency stack moved off the canonical pin (see the docstring),
# NOT that the tolerance should be widened.
REPLAY_ABS_TOL = 1e-6

_PER_FOLD_METRICS = ("ic_1d", "ic_5d", "annualized_return", "max_drawdown", "information_ratio")


def _close(a: float | None, b: float | None) -> bool:
    a = float("nan") if a is None else float(a)
    b = float("nan") if b is None else float(b)
    if math.isnan(a) and math.isnan(b):
        return True
    if math.isnan(a) or math.isnan(b):
        return False
    return abs(a - b) <= REPLAY_ABS_TOL


class WalkForwardReplayBaselineRegen2Tests(unittest.TestCase):
    _result: dict[str, Any]
    _tmpdir: str | None = None
    _silenced: list[tuple[logging.Logger, int]] = []

    @classmethod
    def setUpClass(cls) -> None:
        # qlib is a hard CI dependency (see .github/workflows/test.yml) — this only
        # skips on local no-qlib machines, NOT CI (CI runs the replay for real).
        import importlib.util
        if importlib.util.find_spec("qlib") is None:
            raise unittest.SkipTest("qlib not importable — only skips on no-qlib dev machines.")
        for fixture in (TARBALL, TARBALL_SHA256, FROZEN_FIXTURE, BASELINE_FIXTURE):
            if not fixture.exists():
                raise unittest.SkipTest(f"committed REGEN-2 replay fixture missing: {fixture}")
        # Verify the committed mini-bundle tarball checksum BEFORE trusting it —
        # a mismatch is corrupt/tampered reference data and must fail loudly (CI red),
        # not silently replay against bad bytes.
        expected = TARBALL_SHA256.read_text(encoding="utf-8").split()[0]
        actual = hashlib.sha256(TARBALL.read_bytes()).hexdigest()
        if actual != expected:
            raise AssertionError(
                f"mini-bundle tarball checksum mismatch: {actual} != {expected} "
                "(corrupt/tampered reference data — regenerate via build_regen2_minibundle.py)."
            )
        # Unpack to a temp dir; run the 23-fold replay ONCE for the whole class.
        cls._tmpdir = tempfile.mkdtemp(prefix="regen2_minibundle_")
        with tarfile.open(TARBALL, "r:gz") as tar:
            _safe_extract(tar, Path(cls._tmpdir))
        provider = Path(cls._tmpdir) / _ARCROOT
        namechange = provider / "all_namechanges.parquet"
        # The 23-fold replay emits heavy per-fold qlib/backtest/signal logging;
        # under pytest, capturing tens of thousands of those records dominates
        # wall-time (~5x vs a plain run). Silence the noisy loggers for the replay
        # — the assertions check metrics, not logs; correctness is unaffected.
        cls._silenced = [
            (lg := logging.getLogger(name), lg.level)
            for name in ("qlib", "src.core.backtest_runner", "src.core.signal_analyzer")
        ]
        for lg, _level in cls._silenced:
            lg.setLevel(logging.ERROR)
        from scripts.regen.replay_frozen_baseline_regen2 import replay_frozen_baseline_regen2
        cls._result = replay_frozen_baseline_regen2(FROZEN_FIXTURE, str(provider), str(namechange))

    @classmethod
    def tearDownClass(cls) -> None:
        for lg, level in cls._silenced:
            lg.setLevel(level)
        if cls._tmpdir:
            shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def test_aggregate_reproduces_committed_baseline(self) -> None:
        committed = json.loads(BASELINE_FIXTURE.read_text(encoding="utf-8"))["aggregate_metrics"]
        result = self._result["aggregate_metrics"]
        drifts = []
        for key, ref in committed.items():
            if isinstance(ref, dict):  # nested timing block (wall-clock, non-metric)
                continue
            if not _close(result.get(key), ref):
                drifts.append(f"{key}: replay={result.get(key)!r} vs committed={ref!r}")
        if drifts:
            self.fail(
                "REGEN-2 CI-real replay did NOT reproduce the committed aggregate within "
                f"{REPLAY_ABS_TOL} (deterministic replay against the committed mini-bundle "
                "should be byte-identity):\n  - " + "\n  - ".join(drifts)
                + "\n\nIf a backtest-semantics change is intentional, regenerate via "
                "scripts/regen/replay_frozen_baseline_regen2.py and re-sign the baseline."
            )

    def test_each_fold_reproduces(self) -> None:
        committed_pf = {
            f["fold_index"]: f
            for f in json.loads(BASELINE_FIXTURE.read_text(encoding="utf-8")).get("per_fold", [])
        }
        self.assertTrue(committed_pf, "committed REGEN-2 baseline carries no per_fold block.")
        result = self._result["folds"]
        replay_indices = {fold.fold_index for fold in result}
        self.assertEqual(
            set(committed_pf), replay_indices,
            f"committed per_fold fold set {sorted(committed_pf)} != replay fold set "
            f"{sorted(replay_indices)} — REGEN-2 must be exactly 23 real folds (0..22).",
        )
        drifts = []
        for fold in result:
            ref = committed_pf[fold.fold_index]
            for metric in _PER_FOLD_METRICS:
                if not _close(getattr(fold, metric), ref.get(metric)):
                    drifts.append(
                        f"fold {fold.fold_index}.{metric}: replay={getattr(fold, metric)!r} "
                        f"vs committed={ref.get(metric)!r}"
                    )
        if drifts:
            self.fail(
                "Per-fold metric drift in the REGEN-2 CI-real replay (every fold's "
                f"{', '.join(_PER_FOLD_METRICS)} must reproduce within {REPLAY_ABS_TOL}):\n  - "
                + "\n  - ".join(drifts)
            )


def _safe_extract(tar: tarfile.TarFile, dest: Path) -> None:
    """Extract only regular files under dest (reject path traversal in the committed tarball)."""
    dest = dest.resolve()
    for member in tar.getmembers():
        target = (dest / member.name).resolve()
        # Real containment check (codex P2): a textual ``startswith`` lets a sibling
        # path with the same prefix (e.g. ``/tmp/x_evil`` vs ``/tmp/x``) slip through.
        # ``relative_to`` raises unless ``target`` is genuinely inside ``dest``.
        try:
            target.relative_to(dest)
        except ValueError:
            raise AssertionError(f"unsafe path in mini-bundle tarball: {member.name}") from None
        if member.isfile():
            target.parent.mkdir(parents=True, exist_ok=True)
            src = tar.extractfile(member)
            assert src is not None
            target.write_bytes(src.read())


if __name__ == "__main__":
    unittest.main()
