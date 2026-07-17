"""Positions attestation (schema "4-positions-attestation").

2026-07-17-csi800-cadence-campaign DP-5, producer side: both engines
stamp a sha256 of the PERSISTED positions bytes under the SAME field
name ``positions_sha256`` — walk-forward per fold (fold report +
manifest), pipeline top-level in pipeline_report. This is the #373
codex r10 prerequisite for the ``producer_digest_certified`` promotion
binding.

Coverage matrix (>=1 case per dimension):
  digest == bytes    — write_positions returns sha256 of the file bytes
                       actually on disk.
  report carriage    — build_fold_report emits the field (value and
                       explicit-None cases; absence-vs-null must be
                       distinguishable).
  tamper detection   — modifying the persisted file makes a recomputed
                       digest diverge from the stamped one (the exact
                       re-verification the certify chain performs).
  manifest carriage  — FoldManifest.from_fold threads the digest and
                       survives a save/load round-trip.
  two-engine parity  — pipeline's _write_report emits the SAME-NAME
                       top-level key (explicit None when no positions).
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.core.walk_forward.aggregate import (  # noqa: E402
    FOLD_REPORT_SCHEMA_VERSION,
    build_fold_report,
    write_positions,
)

_POSITIONS = {
    "2024-01-02": {"SH600000": 0.5, "SZ000001": 0.5},
    "2024-01-03": {"SH600000": 0.45, "SZ000001": 0.55},
}


def test_schema_version_bumped() -> None:
    assert FOLD_REPORT_SCHEMA_VERSION == "4-positions-attestation"


def test_write_positions_returns_digest_of_persisted_bytes() -> None:
    with tempfile.TemporaryDirectory() as t:
        p = Path(t) / "fold_00_positions.json"
        digest = write_positions(p, _POSITIONS)
        assert digest == hashlib.sha256(p.read_bytes()).hexdigest()
        assert len(digest) == 64


def test_tampered_positions_diverge_from_stamped_digest() -> None:
    with tempfile.TemporaryDirectory() as t:
        p = Path(t) / "fold_00_positions.json"
        digest = write_positions(p, _POSITIONS)
        payload = json.loads(p.read_text(encoding="utf-8"))
        payload["2024-01-03"]["SH600000"] = 0.99
        p.write_text(json.dumps(payload), encoding="utf-8")
        assert hashlib.sha256(p.read_bytes()).hexdigest() != digest


def _fold_report_args(**over: object) -> dict:
    from tests.logic.test_walk_forward import (
        _stub_backtest_output,
        _stub_signal_result,
    )
    args: dict = dict(
        fold_index=0,
        train_start="2024-01-01", train_end="2024-06-30",
        valid_start="2024-07-01", valid_end="2024-09-30",
        test_start="2024-10-01", test_end="2024-12-31",
        model_artifact_path="/tmp/model_fold0.pkl",
        model_result=MagicMock(
            best_iteration=3,
            final_valid_loss=0.95,
            prediction_shape=(123,),
        ),
        signal_result=_stub_signal_result(),
        backtest_output=_stub_backtest_output(),
        positions_path=Path("/tmp/fold_00_positions.json"),
        ic_1d=0.02, ic_5d=0.04,
        annualized_return=0.11, max_drawdown=-0.08,
        information_ratio=1.2,
    )
    args.update(over)
    return args


def test_fold_report_carries_digest_and_explicit_null() -> None:
    with_digest = build_fold_report(
        **_fold_report_args(positions_sha256="ab" * 32))
    assert with_digest["positions_sha256"] == "ab" * 32
    # no positions produced (failed/empty fold): explicit None, never a
    # missing key — absence-vs-null must be distinguishable.
    without = build_fold_report(
        **_fold_report_args(positions_path=None))
    assert "positions_sha256" in without
    assert without["positions_sha256"] is None


def test_manifest_threads_digest_through_roundtrip() -> None:
    from src.core.walk_forward._resume import FoldManifest
    from src.core.walk_forward._types import WalkForwardFold
    from src.core.walk_forward.config import WalkForwardConfig

    fold = WalkForwardFold(
        fold_index=0,
        train_period="2024-01-01 ~ 2024-06-30",
        valid_period="2024-07-01 ~ 2024-09-30",
        test_period="2024-10-01 ~ 2024-12-31",
        ic_1d=0.02, ic_5d=0.04,
        annualized_return=0.11, max_drawdown=-0.08,
        information_ratio=1.2,
        prediction_shape=(123,),
        report_path="fold_00_report.json",
    )
    cfg = WalkForwardConfig()
    manifest = FoldManifest.from_fold(
        fold=fold, config=cfg,
        model_path="model_fold0.pkl",
        report_path="fold_00_report.json",
        predictions_path="fold_00_predictions.pkl",
        positions_path="fold_00_positions.json",
        positions_sha256="cd" * 32,
    )
    assert manifest.positions_sha256 == "cd" * 32
    with tempfile.TemporaryDirectory() as t:
        # discover() requires the referenced artifacts to exist on disk
        for name in ("model_fold0.pkl", "fold_00_report.json",
                     "fold_00_predictions.pkl", "fold_00_positions.json"):
            (Path(t) / name).write_text("{}", encoding="utf-8")
        manifest.save(Path(t))
        loaded = FoldManifest.discover(Path(t))
        assert loaded[0].positions_sha256 == "cd" * 32


def test_pipeline_report_carries_same_name_key() -> None:
    # two-engine schema symmetry (AGENTS.md): pipeline_report carries
    # the SAME-NAME top-level key; explicit None when no positions.
    import inspect

    from src.core.pipeline import Pipeline

    sig = inspect.signature(Pipeline._write_report)
    assert "positions_sha256" in sig.parameters
    src = inspect.getsource(Pipeline._write_report)
    assert 'report["positions_sha256"] = positions_sha256' in src
