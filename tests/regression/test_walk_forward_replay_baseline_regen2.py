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
A drift past 1e-6 on the STRICT surface (folds 1-22 + fold-0's ICs) means the
dependency stack moved — investigate, do NOT loosen. (The earlier "Windows is the
correct side / qlib cross-OS bug" framing was DISPROVEN: both CI runners agreed; the
split was an off-pin numpy 2.4.4 dev box.)

PER-RUNNER BIMODALITY of fold-0 (CORRECTED — supersedes an earlier "run-to-run flake +
3-attempt retry" framing that was WRONG): fold-0's degeneracy flips its topk selection
between exactly TWO states even on the canonical pin — committed (A) and a recorded
alternate (B) — and the choice is fixed for a whole CI run but VARIES BETWEEN runs (a
fresh GitHub runner can flip it; the divergent value is byte-identical across runs, so it
is a discrete tie-break flip, NOT continuous FP noise). An in-run retry therefore CANNOT
help (all attempts share the runner). So the test ISOLATES fold-0 instead: folds 1-22
(all metrics) and fold-0's ICs reproduce STRICTLY at 1e-6 on every runner (the real
regression surface); fold-0's three topk-dependent backtest metrics (return / drawdown /
IR) and the seven aggregate keys derived from them are asserted against {committed A OR
the known alternate B} (see ``_KNOWN_*_ALT``) — a THIRD value is a real regression and
still fails. The proper fix — a deterministic secondary sort key so the tie-break is
stable — changes the selection and needs a baseline regen; phase-6.

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
BASELINE_FIXTURE = FIXTURES_DIR / "walk_forward_baseline_metrics.json"  # PR-2: REGEN-2 is the canonical root
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


# fold-0's frozen scores are DEGENERATE — ~39 discrete buckets over 300 stocks (261
# ties), so the topk=50 cutoff lands inside a tie block and the selected names depend on
# the sort tie-break. That tie-break is PER-RUNNER bimodal even on the canonical pin (NOT
# merely numpy-major-sensitive): observed byte-identically across CI runs, a fresh GitHub
# runner flips fold-0 between two selections — committed (A) and the recorded alternate
# (B) below. So fold-0's TOPK-DEPENDENT backtest metrics (return / drawdown / IR) — and
# the seven aggregate keys derived from the per-fold IR/ann set — are asserted against
# {committed OR this known alternate}; a THIRD value is still a real regression and fails.
# fold-0's ICs are full-cross-section rank correlations (NOT topk-dependent) and folds
# 1-22 have 300 unique scores (no boundary ties), so they reproduce STRICTLY on every
# runner — unchanged at 1e-6. The proper fix (a deterministic secondary sort key) changes
# the selection -> needs a baseline regen -> phase-6. Do NOT widen 1e-6, and do NOT add a
# third alternate without confirming it is the SAME degeneracy, not a real stack drift.
# The {A, B} set is EMPIRICAL within the numpy<2 range (the pin bounds the major, not the
# exact build); a future numpy<2 patch that partitions the ties differently could surface
# a third selection and red a valid runner — that is the INTENDED fail-loud (investigate;
# the phase-6 deterministic secondary sort key removes the dependence for good).
_FOLD0_DEGENERATE_INDEX = 0
_FOLD0_TOPK_DEPENDENT = ("annualized_return", "max_drawdown", "information_ratio")
_KNOWN_FOLD0_BACKTEST_ALT = {
    "annualized_return": -0.004711347265649301,
    "max_drawdown": -0.02726356962697682,
    "information_ratio": -0.0712889987158074,
}
# The aggregate is a deterministic function of the per-fold IR/ann, so fold-0's flip
# gives each of these exactly two states: committed (A, in the JSON) or alternate (B).
_KNOWN_AGGREGATE_ALT = {
    "mean_annualized_return": 0.028012145880259152,
    "mean_annualized_return_ci_low": -0.04948816928667496,
    "mean_annualized_return_ci_high": 0.09865727070463469,
    "mean_information_ratio": 0.19787663958380639,
    "std_information_ratio": 1.9755829786899575,
    "mean_information_ratio_ci_low": -0.6482102836796528,
    "mean_information_ratio_ci_high": 0.9631212903466849,
}


def _reproduces(replay: float | None, committed: float | None,
                alt: float | None = None) -> bool:
    """True if ``replay`` matches ``committed`` (strict 1e-6) OR — only for the fold-0
    degenerate topk-dependent metrics — the known per-runner alternate selection."""
    if _close(replay, committed):
        return True
    return alt is not None and _close(replay, alt)


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
        # Missing committed reference data is NOT a skip (codex P2): this is the ONLY
        # CI-real guard for the REGEN-2 anchor (it is --ignore'd on every other matrix
        # leg), so an accidentally deleted/mis-checked-out tarball / checksum / frozen
        # scores / baseline must FAIL the leg red, never silent-green skip.
        missing = [str(f) for f in (TARBALL, TARBALL_SHA256, FROZEN_FIXTURE, BASELINE_FIXTURE) if not f.exists()]
        if missing:
            raise AssertionError(
                "committed REGEN-2 replay fixture(s) missing — reference data was deleted "
                f"or not checked out: {missing}. This is the only CI-real anchor guard; a "
                "missing fixture is a hard failure, not a skip."
            )
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
            # _KNOWN_AGGREGATE_ALT carries the fold-0-flip alternate for the seven
            # IR/ann-derived keys; every other key has no alternate -> strict 1e-6.
            alt = _KNOWN_AGGREGATE_ALT.get(key)
            if not _reproduces(result.get(key), ref, alt):
                drifts.append(
                    f"{key}: replay={result.get(key)!r} vs committed={ref!r}"
                    + (f" (or known fold-0-flip alt {alt!r})" if alt is not None else "")
                )
        if drifts:
            self.fail(
                "REGEN-2 CI-real replay did NOT reproduce the committed aggregate within "
                f"{REPLAY_ABS_TOL} (deterministic replay against the committed mini-bundle "
                "should be byte-identity, except fold-0's known per-runner-bimodal "
                "topk-dependent metrics):\n  - " + "\n  - ".join(drifts)
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
                # Only fold-0's topk-dependent backtest metrics get the per-runner
                # alternate; fold-0's ICs and all of folds 1-22 stay strict 1e-6.
                alt = None
                if (fold.fold_index == _FOLD0_DEGENERATE_INDEX
                        and metric in _FOLD0_TOPK_DEPENDENT):
                    alt = _KNOWN_FOLD0_BACKTEST_ALT.get(metric)
                if not _reproduces(getattr(fold, metric), ref.get(metric), alt):
                    drifts.append(
                        f"fold {fold.fold_index}.{metric}: replay={getattr(fold, metric)!r} "
                        f"vs committed={ref.get(metric)!r}"
                        + (f" (or known fold-0-flip alt {alt!r})" if alt is not None else "")
                    )
        if drifts:
            self.fail(
                "Per-fold metric drift in the REGEN-2 CI-real replay (folds 1-22 + fold-0's "
                f"ICs must reproduce within {REPLAY_ABS_TOL}; fold-0's topk-dependent "
                "backtest metrics must match committed OR the known per-runner alt):\n  - "
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
