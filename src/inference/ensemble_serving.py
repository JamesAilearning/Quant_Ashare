"""Strict three-member ensemble serving (PR-A' of
2026-07-20-csi800-n5-production-promotion, R1-DP-A).

The certified campaign evidence generates predictions via the
walk-forward ``apply_ensemble`` (all member models score the CURRENT
dataset; blend = ``concat(axis=1).mean(axis=1, skipna=True)``). This
module reproduces that BLEND MATH for production serving with one
deliberate inversion of failure policy:

* walk-forward degrades gracefully (a broken prior is skipped so one
  bad pickle cannot abort a 23-fold research run);
* production serving FAILS LOUD (spec: manifest 缺员/断链 SHALL
  fail-loud 拒绝出单，绝不静默降级为部分 ensemble 或单模型)。

The serving manifest is the single machine-readable declaration of the
three quarterly members. Its actual first write happens at the PR-C'
cutover; this module ships the schema, the strict loader and the
blending so the cutover consumes tested machinery.

Manifest schema (``csi800_n5_ensemble_manifest_v1``)::

    {
      "schema_version": "csi800_n5_ensemble_manifest_v1",
      "members": [            # exactly 3, OLDEST -> NEWEST by fit_end
        {"pkl_path": "...", "pkl_sha256": "...",
         "fit_start": "YYYY-MM-DD", "fit_end": "YYYY-MM-DD"},
        ...
      ]
    }

Window-arithmetic pins (R1-DP-A/C: 24m rolling train + staggered
quarterly ends) are validated at LOAD, fail-closed — a manifest whose
members do not look like three staggered quarterly retrains is refused
before any model bytes are touched.
"""

from __future__ import annotations

import hashlib
import json
import pickle
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from src.core.logger import get_logger

_logger = get_logger(__name__)

MANIFEST_SCHEMA_VERSION = "csi800_n5_ensemble_manifest_v1"
ENSEMBLE_SIZE = 3
# Staggered quarterly ends: consecutive fit_end gaps must look like one
# quarter (trading-calendar drift tolerated).
MEMBER_SPACING_DAYS_MIN = 75
MEMBER_SPACING_DAYS_MAX = 100
# 24-month rolling train window (calendar drift tolerated).
TRAIN_WINDOW_DAYS_MIN = 700
TRAIN_WINDOW_DAYS_MAX = 745
# Blend identity with the certified walk-forward ensemble.
BLEND = "mean_skipna"


class EnsembleServingError(RuntimeError):
    """Any manifest/member/blend problem — serving refuses to emit a
    list rather than silently degrading (R1-DP-A fail-loud rule)."""


# Which sidecar version key guards which framework — the walk-forward
# ensemble's map (src/core/walk_forward/ensemble.py): ``model_type`` is
# the canonical "which library produced this pickle?" answer, so only
# the matching framework's version is compared (a catboost upgrade must
# not reject an LGB member).
_FRAMEWORK_KEYS = {
    "LGBModel": ("lightgbm_version", "lightgbm"),
    "XGBModel": ("xgboost_version", "xgboost"),
    "CatBoostModel": ("catboost_version", "catboost"),
}


@dataclass(frozen=True)
class EnsembleMember:
    pkl_path: str
    pkl_sha256: str
    # Trainer sidecar (<model>.pkl.meta.json) — part of the member's
    # hash chain (codex #390 r1: the spec's manifest scenario requires
    # missing member pkl/META or a broken chain to fail loud).
    meta_path: str
    meta_sha256: str
    fit_start: str
    fit_end: str


def _read_bytes(path: Path, what: str) -> bytes:
    """Filesystem read wrapped in the serving error (codex #390 r2):
    a directory passed as a path, a permission problem or any other
    OSError must surface as the intended fail-loud refusal, not an
    uncaught traceback the CLI/recommend() cannot classify."""
    try:
        return path.read_bytes()
    except OSError as exc:
        raise EnsembleServingError(
            f"cannot read {what} {path}: "
            f"{type(exc).__name__}: {exc}") from exc


def _parse_day(value: Any, field: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise EnsembleServingError(
            f"manifest member field {field}={value!r} is not a "
            "YYYY-MM-DD date") from exc


def load_ensemble_manifest(
    manifest_path: str | Path,
) -> tuple[tuple[EnsembleMember, ...], str]:
    """Parse + validate the serving manifest, fail-closed.

    Returns ``(members oldest->newest, manifest_sha256)`` — the digest
    is of the manifest bytes actually parsed (single read), for the
    artifact's provenance block.
    """
    p = Path(manifest_path)
    if not p.exists():
        raise EnsembleServingError(f"ensemble manifest not found: {p}")
    raw = _read_bytes(p, "ensemble manifest")
    manifest_sha = hashlib.sha256(raw).hexdigest()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EnsembleServingError(
            f"ensemble manifest is not valid JSON: {p} ({exc})") from exc
    if not isinstance(payload, dict):
        raise EnsembleServingError(
            f"ensemble manifest top level is not an object: {p}")
    if payload.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise EnsembleServingError(
            f"ensemble manifest schema {payload.get('schema_version')!r} "
            f"!= {MANIFEST_SCHEMA_VERSION!r} — refusing.")
    raw_members = payload.get("members")
    if not isinstance(raw_members, list) or len(raw_members) != ENSEMBLE_SIZE:
        raise EnsembleServingError(
            f"ensemble manifest must declare exactly {ENSEMBLE_SIZE} "
            f"members; got "
            f"{len(raw_members) if isinstance(raw_members, list) else type(raw_members).__name__}.")

    members: list[EnsembleMember] = []
    for i, m in enumerate(raw_members):
        if not isinstance(m, dict):
            raise EnsembleServingError(
                f"manifest member[{i}] is not an object.")
        missing = [k for k in ("pkl_path", "pkl_sha256",
                               "meta_path", "meta_sha256",
                               "fit_start", "fit_end") if not m.get(k)]
        if missing:
            raise EnsembleServingError(
                f"manifest member[{i}] missing fields: {missing}.")
        members.append(EnsembleMember(
            pkl_path=str(m["pkl_path"]),
            pkl_sha256=str(m["pkl_sha256"]),
            meta_path=str(m["meta_path"]),
            meta_sha256=str(m["meta_sha256"]),
            fit_start=str(m["fit_start"]),
            fit_end=str(m["fit_end"]),
        ))

    # Window arithmetic pins (R1-DP-A/C), all fail-closed:
    ends = [_parse_day(m.fit_end, "fit_end") for m in members]
    starts = [_parse_day(m.fit_start, "fit_start") for m in members]
    for i in range(1, ENSEMBLE_SIZE):
        if ends[i] <= ends[i - 1]:
            raise EnsembleServingError(
                "manifest members must be ordered oldest->newest with "
                f"strictly increasing fit_end; member[{i}] "
                f"{ends[i]} <= member[{i-1}] {ends[i-1]}.")
        gap = (ends[i] - ends[i - 1]).days
        if not (MEMBER_SPACING_DAYS_MIN <= gap <= MEMBER_SPACING_DAYS_MAX):
            raise EnsembleServingError(
                f"member[{i-1}]->member[{i}] fit_end gap {gap}d outside "
                f"the quarterly stagger pin "
                f"[{MEMBER_SPACING_DAYS_MIN}, {MEMBER_SPACING_DAYS_MAX}].")
    for i in range(ENSEMBLE_SIZE):
        span = (ends[i] - starts[i]).days
        if not (TRAIN_WINDOW_DAYS_MIN <= span <= TRAIN_WINDOW_DAYS_MAX):
            raise EnsembleServingError(
                f"member[{i}] train window {span}d outside the 24-month "
                f"pin [{TRAIN_WINDOW_DAYS_MIN}, {TRAIN_WINDOW_DAYS_MAX}].")

    return tuple(members), manifest_sha


def _check_member_sidecar(
    i: int, meta_path: Path, meta_raw: bytes, member: EnsembleMember,
) -> None:
    """Parse the trainer sidecar and version-guard the member BEFORE its
    pickle is touched (codex #390 r3).

    The walk-forward ensemble parses this same sidecar before unpickling
    because a framework minor bump can silently change booster
    serialisation semantics — the pickle loads without error but behaves
    differently. There the guard SKIPS the prior (research affordance);
    here every tolerance becomes a refusal: unparseable sidecar, missing
    ``pkl_sha256``/``model_type``/version fields, an unimportable
    framework or a version drift all refuse the whole ensemble.
    """
    try:
        sidecar = json.loads(meta_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EnsembleServingError(
            f"ensemble member[{i}] meta sidecar is not valid JSON: "
            f"{meta_path} ({exc}) — cannot version-guard, refusing."
        ) from exc
    if not isinstance(sidecar, dict):
        raise EnsembleServingError(
            f"ensemble member[{i}] meta sidecar top level is not an "
            f"object: {meta_path} — cannot version-guard, refusing.")
    # Three-way chain: the manifest's pkl digest, the sidecar's own
    # pkl_sha256 (written at training time) and the on-disk bytes
    # (checked by the caller) must all agree.
    sidecar_pkl_sha = sidecar.get("pkl_sha256")
    if not isinstance(sidecar_pkl_sha, str) or not sidecar_pkl_sha:
        raise EnsembleServingError(
            f"ensemble member[{i}] meta sidecar carries no pkl_sha256 "
            "— cannot bind sidecar to pickle, refusing.")
    if sidecar_pkl_sha != member.pkl_sha256:
        raise EnsembleServingError(
            f"ensemble member[{i}] sidecar pkl_sha256 {sidecar_pkl_sha} "
            f"!= manifest {member.pkl_sha256} — sidecar describes a "
            "different pickle, refusing.")
    model_type = sidecar.get("model_type")
    if model_type not in _FRAMEWORK_KEYS:
        # walk-forward falls back to a lightgbm-only check for legacy
        # sidecars; serving members are always freshly trainer-produced
        # (R1-DP-A quarterly retrains), so an absent/unknown model_type
        # means the member cannot be version-guarded — refuse.
        raise EnsembleServingError(
            f"ensemble member[{i}] meta sidecar model_type "
            f"{model_type!r} is not one of {sorted(_FRAMEWORK_KEYS)} — "
            "cannot version-guard, refusing.")
    version_key, import_name = _FRAMEWORK_KEYS[model_type]
    sidecar_ver = sidecar.get(version_key)
    if not isinstance(sidecar_ver, str) or not sidecar_ver:
        raise EnsembleServingError(
            f"ensemble member[{i}] meta sidecar carries no "
            f"{version_key} — cannot version-guard, refusing.")
    try:
        module = __import__(import_name)
    except ImportError as exc:
        raise EnsembleServingError(
            f"ensemble member[{i}] was trained with {import_name} "
            f"{sidecar_ver} but {import_name} is not importable in this "
            "serving environment — refusing.") from exc
    current_ver = getattr(module, "__version__", None)
    if sidecar_ver != current_ver:
        raise EnsembleServingError(
            f"ensemble member[{i}] was trained with {import_name} "
            f"{sidecar_ver} but the serving environment has "
            f"{current_ver} — serialization semantics may have "
            "drifted, refusing.")


def load_member_models(
    members: tuple[EnsembleMember, ...],
) -> list[tuple[EnsembleMember, Any]]:
    """Load every member pickle STRICTLY — any missing file, digest
    mismatch, sidecar/version-guard failure, unpickle failure or
    missing ``.predict`` refuses the whole ensemble (never a partial
    one). The digest is computed from the SAME byte buffer that is
    unpickled (single read), mirroring the single-model ``_load_model``
    discipline."""
    loaded: list[tuple[EnsembleMember, Any]] = []
    for i, member in enumerate(members):
        # Member META chain first (codex #390 r1): the trainer sidecar
        # is part of the declared hash chain — absent or drifted meta
        # refuses the whole ensemble before any pickle is touched.
        meta_path = Path(member.meta_path)
        if not meta_path.exists():
            raise EnsembleServingError(
                f"ensemble member[{i}] meta sidecar not found: "
                f"{meta_path} — broken member chain, refusing.")
        meta_raw = _read_bytes(meta_path, f"member[{i}] meta sidecar")
        meta_actual = hashlib.sha256(meta_raw).hexdigest()
        if meta_actual != member.meta_sha256:
            raise EnsembleServingError(
                f"ensemble member[{i}] meta sha256 mismatch: manifest "
                f"{member.meta_sha256} != on-disk {meta_actual} — "
                "sidecar replaced or corrupt, refusing.")
        _check_member_sidecar(i, meta_path, meta_raw, member)
        path = Path(member.pkl_path)
        if not path.exists():
            raise EnsembleServingError(
                f"ensemble member[{i}] pkl not found: {path} — refusing "
                "to serve a partial ensemble.")
        raw = _read_bytes(path, f"member[{i}] pkl")
        actual = hashlib.sha256(raw).hexdigest()
        if actual != member.pkl_sha256:
            raise EnsembleServingError(
                f"ensemble member[{i}] sha256 mismatch: manifest "
                f"{member.pkl_sha256} != on-disk {actual} — model "
                "replaced or corrupt, refusing.")
        try:
            model = pickle.loads(raw)
        except Exception as exc:  # noqa: BLE001 — any unpickle failure refuses
            raise EnsembleServingError(
                f"ensemble member[{i}] failed to unpickle: "
                f"{type(exc).__name__}: {exc}") from exc
        if not hasattr(model, "predict"):
            raise EnsembleServingError(
                f"ensemble member[{i}] object "
                f"{type(model).__name__} has no .predict.")
        loaded.append((member, model))
    return loaded


def ensemble_predict(
    loaded: list[tuple[EnsembleMember, Any]],
    dataset: Any,
    *,
    segment: str = "infer",
) -> Any:
    """Blend all member predictions over ONE dataset — the certified
    ``apply_ensemble`` math (``concat`` + ``mean(axis=1, skipna=True)``)
    with strict serving policy: a member that fails to predict, returns
    a non-Series, or disagrees on the index refuses the run (walk-
    forward's skip-and-continue is a research affordance, not a serving
    one)."""
    import pandas as pd

    frames = []
    reference_index = None
    for i, (_member, model) in enumerate(loaded):
        try:
            pred = model.predict(dataset, segment=segment)
        except Exception as exc:  # noqa: BLE001 — any predict failure refuses
            raise EnsembleServingError(
                f"ensemble member[{i}] predict failed: "
                f"{type(exc).__name__}: {exc}") from exc
        if not isinstance(pred, pd.Series):
            # Coercing a list/ndarray would FABRICATE a default integer
            # index detached from (datetime, instrument) — the blend
            # would mix values across unrelated rows and downstream
            # consumers would see flat numbers instead of stock codes.
            # walk-forward rejects non-Series priors; serving fail-loud
            # policy refuses outright (codex #390 r1).
            raise EnsembleServingError(
                f"ensemble member[{i}] predict returned "
                f"{type(pred).__name__}, expected pd.Series — refusing "
                "to fabricate an index.")
        if reference_index is None:
            reference_index = pred.index
        elif not pred.index.equals(reference_index):
            raise EnsembleServingError(
                f"ensemble member[{i}] returned an index that does not "
                "exactly match member[0] — pandas union-alignment would "
                "silently change the signal universe, refusing.")
        frames.append(pred.rename(f"m{i}"))
    stacked = pd.concat(frames, axis=1)
    blended = stacked.mean(axis=1, skipna=True)
    blended = blended.reindex(reference_index)
    blended.name = None
    _logger.info(
        "ensemble serving: blended %d members (%s).",
        len(frames), BLEND,
    )
    return blended
