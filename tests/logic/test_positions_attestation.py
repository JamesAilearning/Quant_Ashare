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
    with tempfile.TemporaryDirectory() as t:
        # discover() requires the referenced artifacts to exist on disk
        # — and (since codex #375 r3) the attested positions bytes to
        # still hash to the recorded digest, so use the REAL digest.
        for name in ("model_fold0.pkl", "fold_00_report.json",
                     "fold_00_predictions.pkl"):
            (Path(t) / name).write_text("{}", encoding="utf-8")
        digest = write_positions(
            Path(t) / "fold_00_positions.json", _POSITIONS)
        manifest = FoldManifest.from_fold(
            fold=fold, config=cfg,
            model_path="model_fold0.pkl",
            report_path="fold_00_report.json",
            predictions_path="fold_00_predictions.pkl",
            positions_path="fold_00_positions.json",
            positions_sha256=digest,
        )
        assert manifest.positions_sha256 == digest
        manifest.save(Path(t))
        loaded = FoldManifest.discover(Path(t))
        assert loaded[0].positions_sha256 == digest


def test_resume_rejects_missing_or_mutated_attested_positions() -> None:
    # codex #375 r3: an AUTO resume must not silently reuse a fold whose
    # attested positions were deleted or mutated after the run — the
    # manifest is rejected so the fold re-runs.
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

    def _mk(t: Path) -> None:
        for name in ("model_fold0.pkl", "fold_00_report.json",
                     "fold_00_predictions.pkl"):
            (t / name).write_text("{}", encoding="utf-8")

    def _manifest(digest: str) -> FoldManifest:
        return FoldManifest.from_fold(
            fold=fold, config=WalkForwardConfig(),
            model_path="model_fold0.pkl",
            report_path="fold_00_report.json",
            predictions_path="fold_00_predictions.pkl",
            positions_path="fold_00_positions.json",
            positions_sha256=digest,
        )

    # intact: digest matches -> manifest accepted
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        _mk(td)
        digest = write_positions(td / "fold_00_positions.json", _POSITIONS)
        _manifest(digest).save(td)
        assert 0 in FoldManifest.discover(td)

    # mutated after the run: digest mismatch -> rejected (fold re-runs)
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        _mk(td)
        digest = write_positions(td / "fold_00_positions.json", _POSITIONS)
        _manifest(digest).save(td)
        (td / "fold_00_positions.json").write_text("{}", encoding="utf-8")
        assert 0 not in FoldManifest.discover(td)

    # deleted after the run: recorded positions missing -> rejected
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        _mk(td)
        digest = write_positions(td / "fold_00_positions.json", _POSITIONS)
        _manifest(digest).save(td)
        (td / "fold_00_positions.json").unlink()
        assert 0 not in FoldManifest.discover(td)


def test_unreadable_attested_positions_marks_manifest_invalid() -> None:
    # codex #375 r4: present-but-unreadable (ACL/I-O error) must degrade
    # to the invalid-manifest path, not escape as OSError and abort the
    # whole run before the fold loop.
    from unittest import mock

    from src.core.walk_forward._resume import (
        FoldManifest,
        _missing_required_artifacts,
    )
    from src.core.walk_forward._types import WalkForwardFold
    from src.core.walk_forward.config import WalkForwardConfig

    fold = WalkForwardFold(
        fold_index=0,
        train_period="a ~ b", valid_period="c ~ d", test_period="e ~ f",
        ic_1d=0.01, ic_5d=0.02, annualized_return=0.1,
        max_drawdown=-0.05, information_ratio=0.5,
        prediction_shape=(10,), report_path="fold_00_report.json",
    )
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        for name in ("model_fold0.pkl", "fold_00_report.json",
                     "fold_00_predictions.pkl"):
            (td / name).write_text("{}", encoding="utf-8")
        digest = write_positions(td / "fold_00_positions.json", _POSITIONS)
        manifest = FoldManifest.from_fold(
            fold=fold, config=WalkForwardConfig(),
            model_path=str(td / "model_fold0.pkl"),
            report_path=str(td / "fold_00_report.json"),
            predictions_path=str(td / "fold_00_predictions.pkl"),
            positions_path=str(td / "fold_00_positions.json"),
            positions_sha256=digest,
        ).with_paths_rebased(td)
        with mock.patch.object(
            Path, "read_bytes", side_effect=OSError("permission denied"),
        ):
            missing = _missing_required_artifacts(manifest)
        assert any("unreadable" in m for m in missing)


def _pipeline_report_stubs() -> tuple[object, object, object, object, object]:
    """Minimal stubs for Pipeline._write_report (mirrors the pattern in
    tests/logic/test_pipeline.py ReportGitProvenanceTests)."""
    from types import SimpleNamespace

    from src.core.signal_analyzer import SignalAnalysisResult

    config = SimpleNamespace(
        instruments="csi300", feature_handler="alpha158",
        label_horizon_days=1,
        train_start="2022-01-01", train_end="2022-12-31",
        valid_start="2023-01-01", valid_end="2023-03-31",
        test_start="2023-04-01", test_end="2023-06-30",
        model_type="LGBModel", benchmark_code="SH000300",
        topk=50, n_drop=5, industry_taxonomy_id=None,
        attribution_sleeve_grouping=False,
        risk_constraints_enabled=False,
        risk_constraints_calibration="default",
        risk_constraint_scope="all_days",
        delisted_registry_path="",
    )
    feature_result = SimpleNamespace(
        train_shape=(10, 5), valid_shape=(5, 5), test_shape=(5, 5),
    )
    model_result = SimpleNamespace(
        prediction_shape=(5, 1), model_artifact_path="m.pkl",
    )
    signal_result = SignalAnalysisResult(
        ic_summary={1: {"mean_ic": 0.01, "std_ic": 0.02, "ir": 0.5,
                        "num_days": 5}},
        ic_series={}, ic_decay=[0.01], turnover_stats={"mean_turnover": 0.1},
    )
    backtest_output = SimpleNamespace(
        metric_status="ok", official_backtest_path="official",
        report={}, provenance={}, risk_analysis={},
    )
    return config, feature_result, model_result, signal_result, backtest_output


def test_pipeline_persist_and_report_roundtrip() -> None:
    # two-engine schema symmetry (AGENTS.md), exercised through the REAL
    # write path (codex #375 r1): persist positions via the same helper
    # Pipeline.run uses, recompute the digest from the bytes on disk,
    # thread it through the real report writer, and read it back.
    from src.core.pipeline import Pipeline

    cfg, feat, model, sig, bt = _pipeline_report_stubs()
    with tempfile.TemporaryDirectory() as t:
        out = Path(t)
        p_path, digest = Pipeline._persist_positions(out, _POSITIONS)
        assert p_path == out / "positions.json"
        assert digest == hashlib.sha256(p_path.read_bytes()).hexdigest()

        report_path = out / "pipeline_report.json"
        Pipeline._write_report(
            str(report_path), cfg, feat, model, sig, bt,
            factor_skipped_reason="unit-test",
            git_provenance={"commit": "cafebabe" * 5, "dirty": False},
            positions_sha256=digest,
        )
        data = json.loads(report_path.read_text(encoding="utf-8"))
        assert data["positions_sha256"] == digest

        # tampering the persisted series breaks re-verification against
        # the emitted report field — the exact certify-chain check.
        payload = json.loads(p_path.read_text(encoding="utf-8"))
        payload["2024-01-03"]["SH600000"] = 0.99
        p_path.write_text(json.dumps(payload), encoding="utf-8")
        assert (hashlib.sha256(p_path.read_bytes()).hexdigest()
                != data["positions_sha256"])


def test_pipeline_report_explicit_null_without_positions() -> None:
    # no positions persisted -> the same-name key is present as an
    # explicit None (absence-vs-null must be distinguishable).
    from src.core.pipeline import Pipeline

    cfg, feat, model, sig, bt = _pipeline_report_stubs()
    with tempfile.TemporaryDirectory() as t:
        report_path = Path(t) / "pipeline_report.json"
        Pipeline._write_report(
            str(report_path), cfg, feat, model, sig, bt,
            factor_skipped_reason="unit-test",
            git_provenance={"commit": "cafebabe" * 5, "dirty": False},
        )
        data = json.loads(report_path.read_text(encoding="utf-8"))
        assert "positions_sha256" in data
        assert data["positions_sha256"] is None
