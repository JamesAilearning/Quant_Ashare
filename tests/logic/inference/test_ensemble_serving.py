"""Strict ensemble serving (PR-A' of
2026-07-20-csi800-n5-production-promotion, R1-DP-A).

Coverage matrix (>=1 case per dimension):

  manifest schema  — happy path / wrong version / wrong count / missing
                     fields / bad ordering / bad stagger / bad window.
  member loading   — missing pkl / sha mismatch / unpickle failure /
                     no .predict — ALL refuse (never partial).
  sidecar guard    — unparseable / missing pkl_sha256 / sidecar-manifest
                     contradiction / unknown model_type / missing or
                     drifted framework version — ALL refuse (the
                     walk-forward version guard with skip → refuse,
                     codex #390 r3).
  blend identity   — mean-skipna over aligned series, equal to the
                     walk-forward apply_ensemble math on the same
                     inputs; NaN handled per-member (skipna).
  strict predict   — member predict failure refuses; index mismatch
                     refuses (no union-alignment).
"""

from __future__ import annotations

import hashlib
import json
import pickle
import sys
from pathlib import Path

import pandas as pd
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.inference.ensemble_serving import (  # noqa: E402
    ENSEMBLE_SIZE,
    MANIFEST_SCHEMA_VERSION,
    EnsembleServingError,
    ensemble_predict,
    load_ensemble_manifest,
    load_member_models,
)

# Three staggered quarterly members mirroring R1-DP-C arithmetic
# (24m windows, ends ~1 quarter apart).
_WINDOWS = [
    ("2022-12-20", "2024-12-18"),
    ("2023-03-20", "2025-03-18"),
    ("2023-06-20", "2025-06-18"),
]


class _StubModel:
    """Pickle-able stub with a deterministic predict."""

    def __init__(self, offset: float) -> None:
        self.offset = offset

    def predict(self, dataset, segment="infer"):  # noqa: ANN001
        idx = pd.MultiIndex.from_product(
            [pd.to_datetime(["2025-07-01"]), ["S0", "S1", "S2"]])
        return pd.Series([1.0 + self.offset, 2.0 + self.offset,
                          3.0 + self.offset], index=idx)


class _BoomModel(_StubModel):
    """Pickle-able member whose predict always raises."""

    def predict(self, dataset, segment="infer"):  # noqa: ANN001
        raise RuntimeError("boom")


class _OtherIndexModel(_StubModel):
    """Pickle-able member returning a mismatched index."""

    def predict(self, dataset, segment="infer"):  # noqa: ANN001
        idx = pd.MultiIndex.from_product(
            [pd.to_datetime(["2025-07-01"]), ["S0", "S1", "S9"]])
        return pd.Series([1.0, 2.0, 3.0], index=idx)


class _ListModel(_StubModel):
    """Pickle-able member returning a bare list (no index)."""

    def predict(self, dataset, segment="infer"):  # noqa: ANN001
        return [1.0, 2.0, 3.0]


def _write_member(tmp: Path, i: int, obj: object) -> dict:
    # The version guard (codex #390 r3) compares the sidecar's framework
    # version against the installed one — the fast suite already binds
    # to lightgbm (tests/logic/test_walk_forward_ensemble.py).
    import lightgbm

    pkl = tmp / f"member_{i}.pkl"
    pkl.write_bytes(pickle.dumps(obj))
    meta = tmp / f"member_{i}.pkl.meta.json"
    meta.write_text(json.dumps({
        "schema_version": "v1", "model_type": "LGBModel",
        "best_iteration": 100 + i, "num_boost_round": 1000,
        "lightgbm_version": lightgbm.__version__,
        "pkl_sha256": hashlib.sha256(pkl.read_bytes()).hexdigest(),
    }), encoding="utf-8")
    return {
        "pkl_path": str(pkl),
        "pkl_sha256": hashlib.sha256(pkl.read_bytes()).hexdigest(),
        "meta_path": str(meta),
        "meta_sha256": hashlib.sha256(meta.read_bytes()).hexdigest(),
        "fit_start": _WINDOWS[i][0],
        "fit_end": _WINDOWS[i][1],
    }


def _write_manifest(tmp: Path, members: list[dict], **over) -> Path:
    payload = {"schema_version": MANIFEST_SCHEMA_VERSION,
               "members": members, **over}
    p = tmp / "manifest.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def _happy_manifest(tmp: Path) -> tuple[Path, list[dict]]:
    members = [_write_member(tmp, i, _StubModel(float(i)))
               for i in range(ENSEMBLE_SIZE)]
    return _write_manifest(tmp, members), members


def _resync_sidecar(member: dict, text: str) -> None:
    """Overwrite a member's sidecar and re-sync the manifest entry's
    meta_sha256 so the corruption reaches the sidecar PARSE/version
    guard (codex #390 r3) instead of the earlier meta-hash check."""
    p = Path(member["meta_path"])
    p.write_text(text, encoding="utf-8")
    member["meta_sha256"] = hashlib.sha256(p.read_bytes()).hexdigest()


def _sidecar_payload(member: dict) -> dict:
    return json.loads(
        Path(member["meta_path"]).read_text(encoding="utf-8"))


def test_happy_manifest_loads_and_hashes(tmp_path: Path) -> None:
    mp, _ = _happy_manifest(tmp_path)
    members, sha = load_ensemble_manifest(mp)
    assert len(members) == ENSEMBLE_SIZE
    assert sha == hashlib.sha256(mp.read_bytes()).hexdigest()
    assert members[-1].fit_end == _WINDOWS[-1][1]


def test_directory_as_manifest_refused_with_serving_error(
        tmp_path: Path) -> None:
    # codex #390 r2: a path that exists but is not a readable file
    # (directory / permission problem) must surface as the serving
    # error, never a raw IsADirectoryError/OSError traceback.
    with pytest.raises(EnsembleServingError, match="cannot read"):
        load_ensemble_manifest(tmp_path)


def test_wrong_schema_version_refused(tmp_path: Path) -> None:
    mp, members = _happy_manifest(tmp_path)
    mp.write_text(json.dumps({"schema_version": "v0",
                              "members": members}), encoding="utf-8")
    with pytest.raises(EnsembleServingError, match="schema"):
        load_ensemble_manifest(mp)


def test_wrong_member_count_refused(tmp_path: Path) -> None:
    _, members = _happy_manifest(tmp_path)
    mp = _write_manifest(tmp_path, members[:2])
    with pytest.raises(EnsembleServingError, match="exactly"):
        load_ensemble_manifest(mp)


def test_missing_field_refused(tmp_path: Path) -> None:
    _, members = _happy_manifest(tmp_path)
    del members[1]["pkl_sha256"]
    mp = _write_manifest(tmp_path, members)
    with pytest.raises(EnsembleServingError, match="missing fields"):
        load_ensemble_manifest(mp)


def test_non_increasing_fit_end_refused(tmp_path: Path) -> None:
    _, members = _happy_manifest(tmp_path)
    members[2]["fit_end"] = members[1]["fit_end"]
    mp = _write_manifest(tmp_path, members)
    with pytest.raises(EnsembleServingError, match="strictly increasing"):
        load_ensemble_manifest(mp)


def test_bad_quarterly_stagger_refused(tmp_path: Path) -> None:
    _, members = _happy_manifest(tmp_path)
    members[2]["fit_start"] = "2023-03-25"
    members[2]["fit_end"] = "2025-03-23"   # 5d after member[1] — not a quarter
    mp = _write_manifest(tmp_path, members)
    with pytest.raises(EnsembleServingError, match="stagger"):
        load_ensemble_manifest(mp)


def test_bad_train_window_refused(tmp_path: Path) -> None:
    _, members = _happy_manifest(tmp_path)
    members[0]["fit_start"] = "2024-06-20"  # ~6 months, not 24
    mp = _write_manifest(tmp_path, members)
    with pytest.raises(EnsembleServingError, match="24-month"):
        load_ensemble_manifest(mp)


def test_missing_meta_sidecar_refuses(tmp_path: Path) -> None:
    # codex #390 r1: the member META is part of the declared hash
    # chain — an absent sidecar refuses the whole ensemble.
    mp, members = _happy_manifest(tmp_path)
    Path(members[0]["meta_path"]).unlink()
    loaded_members, _ = load_ensemble_manifest(mp)
    with pytest.raises(EnsembleServingError, match="meta sidecar not found"):
        load_member_models(loaded_members)


def test_meta_sha_mismatch_refuses(tmp_path: Path) -> None:
    mp, members = _happy_manifest(tmp_path)
    Path(members[1]["meta_path"]).write_text("{}", encoding="utf-8")
    loaded_members, _ = load_ensemble_manifest(mp)
    with pytest.raises(EnsembleServingError, match="meta sha256 mismatch"):
        load_member_models(loaded_members)


def test_manifest_missing_meta_fields_refused(tmp_path: Path) -> None:
    _, members = _happy_manifest(tmp_path)
    del members[2]["meta_sha256"]
    mp = _write_manifest(tmp_path, members)
    with pytest.raises(EnsembleServingError, match="missing fields"):
        load_ensemble_manifest(mp)


# ── sidecar version guard (codex #390 r3) ──────────────────────────
# Every tolerance of the walk-forward sidecar guard becomes a refusal
# here: unparseable sidecar / missing pkl_sha256 / sidecar-manifest
# contradiction / unknown model_type / missing version / version drift.

def test_sidecar_not_json_refuses(tmp_path: Path) -> None:
    _, members = _happy_manifest(tmp_path)
    _resync_sidecar(members[0], "not json {")
    mp = _write_manifest(tmp_path, members)
    loaded_members, _ = load_ensemble_manifest(mp)
    with pytest.raises(EnsembleServingError, match="not valid JSON"):
        load_member_models(loaded_members)


def test_sidecar_non_object_refuses(tmp_path: Path) -> None:
    _, members = _happy_manifest(tmp_path)
    _resync_sidecar(members[1], json.dumps(["a", "list"]))
    mp = _write_manifest(tmp_path, members)
    loaded_members, _ = load_ensemble_manifest(mp)
    with pytest.raises(EnsembleServingError, match="not an\\s+object"):
        load_member_models(loaded_members)


def test_sidecar_missing_pkl_sha_refuses(tmp_path: Path) -> None:
    _, members = _happy_manifest(tmp_path)
    payload = _sidecar_payload(members[0])
    del payload["pkl_sha256"]
    _resync_sidecar(members[0], json.dumps(payload))
    mp = _write_manifest(tmp_path, members)
    loaded_members, _ = load_ensemble_manifest(mp)
    with pytest.raises(EnsembleServingError, match="no pkl_sha256"):
        load_member_models(loaded_members)


def test_sidecar_pkl_sha_contradicts_manifest_refuses(
        tmp_path: Path) -> None:
    # Three-way chain: manifest digest == on-disk bytes, but the sidecar
    # says it belongs to a DIFFERENT pickle — the chain is internally
    # inconsistent, refuse.
    _, members = _happy_manifest(tmp_path)
    payload = _sidecar_payload(members[2])
    payload["pkl_sha256"] = "0" * 64
    _resync_sidecar(members[2], json.dumps(payload))
    mp = _write_manifest(tmp_path, members)
    loaded_members, _ = load_ensemble_manifest(mp)
    with pytest.raises(EnsembleServingError,
                       match="describes a\\s+different pickle"):
        load_member_models(loaded_members)


def test_sidecar_unknown_model_type_refuses(tmp_path: Path) -> None:
    # walk-forward falls back to a lightgbm-only check for legacy
    # sidecars; serving refuses what it cannot version-guard.
    _, members = _happy_manifest(tmp_path)
    payload = _sidecar_payload(members[1])
    payload["model_type"] = "MysteryModel"
    _resync_sidecar(members[1], json.dumps(payload))
    mp = _write_manifest(tmp_path, members)
    loaded_members, _ = load_ensemble_manifest(mp)
    with pytest.raises(EnsembleServingError, match="model_type"):
        load_member_models(loaded_members)


def test_sidecar_missing_model_type_refuses(tmp_path: Path) -> None:
    _, members = _happy_manifest(tmp_path)
    payload = _sidecar_payload(members[1])
    del payload["model_type"]
    _resync_sidecar(members[1], json.dumps(payload))
    mp = _write_manifest(tmp_path, members)
    loaded_members, _ = load_ensemble_manifest(mp)
    with pytest.raises(EnsembleServingError, match="model_type"):
        load_member_models(loaded_members)


def test_sidecar_missing_framework_version_refuses(
        tmp_path: Path) -> None:
    _, members = _happy_manifest(tmp_path)
    payload = _sidecar_payload(members[0])
    del payload["lightgbm_version"]
    _resync_sidecar(members[0], json.dumps(payload))
    mp = _write_manifest(tmp_path, members)
    loaded_members, _ = load_ensemble_manifest(mp)
    with pytest.raises(EnsembleServingError, match="no\\s+lightgbm_version"):
        load_member_models(loaded_members)


def test_sidecar_framework_version_drift_refuses(tmp_path: Path) -> None:
    # A framework minor bump can silently change booster serialisation
    # semantics — the certified ensemble must not emit recommendations
    # under a different framework version (codex #390 r3 P1).
    _, members = _happy_manifest(tmp_path)
    payload = _sidecar_payload(members[2])
    payload["lightgbm_version"] = "0.0.0-drifted"
    _resync_sidecar(members[2], json.dumps(payload))
    mp = _write_manifest(tmp_path, members)
    loaded_members, _ = load_ensemble_manifest(mp)
    with pytest.raises(EnsembleServingError, match="0.0.0-drifted"):
        load_member_models(loaded_members)


def test_non_series_prediction_refused(tmp_path: Path) -> None:
    # codex #390 r1: coercing a list/ndarray fabricates a default
    # integer index detached from (datetime, instrument) — refuse.
    members = [_write_member(tmp_path, 0, _StubModel(0.0)),
               _write_member(tmp_path, 1, _ListModel(1.0)),
               _write_member(tmp_path, 2, _StubModel(2.0))]
    mp = _write_manifest(tmp_path, members)
    loaded_members, _ = load_ensemble_manifest(mp)
    loaded = load_member_models(loaded_members)
    with pytest.raises(EnsembleServingError, match="expected pd.Series"):
        ensemble_predict(loaded, dataset=None)


def test_missing_pkl_refuses_whole_ensemble(tmp_path: Path) -> None:
    mp, members = _happy_manifest(tmp_path)
    Path(members[1]["pkl_path"]).unlink()
    loaded_members, _ = load_ensemble_manifest(mp)
    with pytest.raises(EnsembleServingError, match="not found"):
        load_member_models(loaded_members)


def test_sha_mismatch_refuses(tmp_path: Path) -> None:
    mp, members = _happy_manifest(tmp_path)
    Path(members[2]["pkl_path"]).write_bytes(
        pickle.dumps(_StubModel(99.0)))  # swapped after manifest
    loaded_members, _ = load_ensemble_manifest(mp)
    with pytest.raises(EnsembleServingError, match="sha256 mismatch"):
        load_member_models(loaded_members)


def test_object_without_predict_refuses(tmp_path: Path) -> None:
    members = [_write_member(tmp_path, i, _StubModel(float(i)))
               for i in range(2)]
    members.append(_write_member(tmp_path, 2, {"not": "a model"}))
    mp = _write_manifest(tmp_path, members)
    loaded_members, _ = load_ensemble_manifest(mp)
    with pytest.raises(EnsembleServingError, match="no .predict"):
        load_member_models(loaded_members)


def test_blend_is_mean_skipna_identity(tmp_path: Path) -> None:
    # The serving blend must equal the certified apply_ensemble math:
    # concat + mean(axis=1, skipna=True) over exactly-aligned series.
    mp, _ = _happy_manifest(tmp_path)
    members, _sha = load_ensemble_manifest(mp)
    loaded = load_member_models(members)
    blended = ensemble_predict(loaded, dataset=None)
    # members offsets 0,1,2 -> mean offset 1.0 over base [1,2,3]
    assert list(blended) == [2.0, 3.0, 4.0]

    # NaN per-member is skipna-averaged, mirroring apply_ensemble.
    frames = [pd.Series([1.0, float("nan")]).rename("m0"),
              pd.Series([3.0, 5.0]).rename("m1")]
    stacked = pd.concat(frames, axis=1)
    assert list(stacked.mean(axis=1, skipna=True)) == [2.0, 5.0]


def test_member_predict_failure_refuses(tmp_path: Path) -> None:
    members = [_write_member(tmp_path, 0, _StubModel(0.0)),
               _write_member(tmp_path, 1, _BoomModel(1.0)),
               _write_member(tmp_path, 2, _StubModel(2.0))]
    mp = _write_manifest(tmp_path, members)
    loaded_members, _ = load_ensemble_manifest(mp)
    loaded = load_member_models(loaded_members)
    with pytest.raises(EnsembleServingError, match="predict failed"):
        ensemble_predict(loaded, dataset=None)


def test_recommend_refuses_fit_window_mismatch(tmp_path: Path) -> None:
    # daily_recommend wiring: ensemble mode pins the config fit window
    # to the NEWEST member (certified current-fold normalization) —
    # a mismatch refuses BEFORE any provider/qlib touch, so this is
    # unit-testable with bogus paths.
    from src.inference.daily_recommend import (
        DailyRecommendationError,
        RecommendationConfig,
        recommend,
    )

    mp, _ = _happy_manifest(tmp_path)
    config = RecommendationConfig(
        model_path=str(mp),
        provider_uri="Z:/nonexistent/bundle",
        delisted_registry_path="Z:/nonexistent/reg.parquet",
        fit_start="2018-01-02", fit_end="2024-12-18",  # != newest member
        ensemble_manifest_path=str(mp),
    )
    with pytest.raises(DailyRecommendationError, match="fit window"):
        recommend(config)


def test_run_meta_carries_ensemble_provenance(tmp_path: Path) -> None:
    # The artifact meta must bind the list to the exact manifest and
    # member pickles (model_path points at the manifest; members
    # enumerated verbatim). model_pkl_sha256 is RESERVED for the
    # single-pickle digest (the decision page cross-checks it against
    # the trainer sidecar) — an ensemble artifact OMITS it and its
    # identity is meta.ensemble.manifest_sha256 (codex #390 r3).
    # Single-model shape untouched — pinned by the existing run-meta
    # tests.
    from src.inference.daily_recommend import (
        RecommendationConfig,
        _assemble_run_meta,
    )

    mp, members = _happy_manifest(tmp_path)
    config = RecommendationConfig(
        model_path=str(mp),
        provider_uri="Z:/x", delisted_registry_path="Z:/y",
        fit_start=_WINDOWS[-1][0], fit_end=_WINDOWS[-1][1],
        ensemble_manifest_path=str(mp),
    )
    manifest_sha = hashlib.sha256(mp.read_bytes()).hexdigest()
    meta = _assemble_run_meta(
        config, model_pkl_sha256=None, bundle_tag=None,
        generated_at="2026-07-22T09:00:00+08:00",
        ensemble={
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "manifest_path": str(mp),
            "manifest_sha256": manifest_sha,
            "blend": "mean_skipna",
            "n_models": 3,
            "members": members,
        },
    )
    assert meta["model_path"] == str(mp)
    assert "model_pkl_sha256" not in meta
    assert meta["ensemble"]["manifest_sha256"] == manifest_sha
    assert meta["ensemble"]["n_models"] == 3
    assert len(meta["ensemble"]["members"]) == 3
    assert meta["ensemble"]["members"][0]["pkl_sha256"]


def test_run_meta_requires_exactly_one_identity_source() -> None:
    # model_pkl_sha256 XOR ensemble — passing both (or neither) is a
    # programming error the assembler refuses instead of emitting an
    # artifact with ambiguous identity (codex #390 r3).
    from src.inference.daily_recommend import (
        RecommendationConfig,
        _assemble_run_meta,
    )

    config = RecommendationConfig(
        model_path="Z:/m.pkl",
        provider_uri="Z:/x", delisted_registry_path="Z:/y",
        fit_start=_WINDOWS[-1][0], fit_end=_WINDOWS[-1][1],
    )
    with pytest.raises(ValueError, match="exactly one identity"):
        _assemble_run_meta(
            config, model_pkl_sha256=None, bundle_tag=None,
            generated_at="2026-07-22T09:00:00+08:00")
    with pytest.raises(ValueError, match="exactly one identity"):
        _assemble_run_meta(
            config, model_pkl_sha256="a" * 64, bundle_tag=None,
            generated_at="2026-07-22T09:00:00+08:00",
            ensemble={"manifest_path": "Z:/m.json",
                      "manifest_sha256": "b" * 64})


def test_index_mismatch_refuses(tmp_path: Path) -> None:
    members = [_write_member(tmp_path, 0, _StubModel(0.0)),
               _write_member(tmp_path, 1, _OtherIndexModel(1.0)),
               _write_member(tmp_path, 2, _StubModel(2.0))]
    mp = _write_manifest(tmp_path, members)
    loaded_members, _ = load_ensemble_manifest(mp)
    loaded = load_member_models(loaded_members)
    with pytest.raises(EnsembleServingError, match="index"):
        ensemble_predict(loaded, dataset=None)
