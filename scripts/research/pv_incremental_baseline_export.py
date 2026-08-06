"""pv_incremental_v1 baseline-prediction exporter (PV-DP-3).

Consumes a COMPLETED walk-forward run directory (the one produced by
``config/presets/pv_incremental_baseline.yaml``) and emits the two
artifacts the campaign binds to:

* ``<out>/baseline_preds.parquet`` — wide (date × instrument)
  out-of-fold predictions, concatenated across folds;
* ``<out>/baseline_preds.parquet.provenance.json`` — the sidecar the
  GP miner and the OOS evaluator both REQUIRE before a baseline may
  key the campaign's only incremental criterion.

Every check below refuses rather than exports: a baseline that
silently carries holdout rows, mixed commits, a dirty tree, a
tampered fold pickle, or a non-frozen ensemble semantics would
invalidate every downstream fitness score and OOS verdict.

Ignition (operator): run the walk-forward FIRST in one uninterrupted
invocation, then this exporter. Neither is auto-run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pickle
import re
import sys
from dataclasses import fields
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

PROTOCOL_ID = "pv_incremental_v1"
PLAN_PATH = _REPO_ROOT / "docs" / "prereg" / "pv_incremental.yaml"


class PVBaselineError(RuntimeError):
    """Domain error: the baseline export refuses."""


def load_frozen_plan(path: Path = PLAN_PATH) -> dict[str, Any]:
    """Load + verify the frozen plan (same discipline as the
    evaluator): foreign protocol or an unblinded holdout refuses."""
    plan = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(plan, dict):
        raise PVBaselineError(f"frozen plan {path} is not a mapping.")
    if plan.get("protocol_id") != PROTOCOL_ID:
        raise PVBaselineError(
            f"frozen plan carries protocol_id "
            f"{plan.get('protocol_id')!r} — refusing.")
    if plan.get("holdout_unblinded") is not False:
        raise PVBaselineError(
            "frozen plan holdout_unblinded is not False — the baseline "
            "must be generated under a BLINDED holdout; refusing.")
    return plan


def check_run_provenance(agg_report: dict[str, Any]) -> dict[str, Any]:
    """The baseline run's git provenance must be clean and singular.

    A resumed run mixing commits resolves ``git_commit`` to null (the
    engine warns), and a dirty tree means the exported numbers cannot
    be reproduced from any commit. Both refuse: the ledger entry would
    be unfalsifiable.
    """
    commit = agg_report.get("git_commit")
    dirty = agg_report.get("git_dirty")
    if not isinstance(commit, str) or not re.fullmatch(
            r"[0-9a-f]{40}", commit):
        raise PVBaselineError(
            f"walk-forward report git_commit={commit!r} is not a full "
            "40-hex sha — a resumed run mixing commits resolves to "
            "null; re-run ALL folds in ONE invocation; refusing.")
    if dirty is not False:
        raise PVBaselineError(
            f"walk-forward report git_dirty={dirty!r} — the baseline "
            "must come from a clean tree so the ledger entry is "
            "reproducible; refusing.")
    return {"source_git": commit, "source_git_dirty": False}


def resolved_config_sha256(cfg: dict[str, Any]) -> str:
    """Seal the RESOLVED config the engine actually captured.

    The preset is only an ``extends`` child (codex #401 r5): hashing
    its bytes leaves every inherited field — train/valid/test months,
    model hyperparameters, seed, ST mask, slippage — unsealed, so a
    run driven by a mutated parent passes the five-field check with an
    unchanged child hash and the sidecar claims a binding it does not
    have. The aggregate report's embedded config IS the resolved
    settings that produced these predictions, so that is what the
    sidecar seals.
    """
    canonical = json.dumps(cfg, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def config_chain_sha256(preset_path: Path) -> dict[str, str]:
    """sha256 of every file in the preset's ``extends`` chain.

    Complements the resolved-config seal: it names WHICH file changed
    when a future re-export disagrees, instead of only proving that
    something did. Cycles and missing parents refuse rather than
    silently truncating the chain.
    """
    chain: dict[str, str] = {}
    seen: set[Path] = set()
    current = preset_path.resolve()
    while True:
        if current in seen:
            raise PVBaselineError(
                f"config extends chain cycles at {current}; refusing.")
        seen.add(current)
        if not current.is_file():
            raise PVBaselineError(
                f"config chain references {current} which does not "
                "exist; refusing.")
        raw = current.read_bytes()
        try:
            rel = str(current.relative_to(_REPO_ROOT)).replace("\\", "/")
        except ValueError:
            rel = str(current)
        chain[rel] = hashlib.sha256(raw).hexdigest()
        doc = yaml.safe_load(raw.decode("utf-8"))
        parent = doc.get("extends") if isinstance(doc, dict) else None
        if not parent:
            return chain
        current = (current.parent / str(parent)).resolve()


def bind_source_bundle(provider_uri: str,
                       wide_index: pd.Index) -> dict[str, Any]:
    """Bind the export to the DATA that produced it (codex #401 r12).

    The aggregate report captures only ``WalkForwardConfig``; the
    runtime ``provider_uri`` and the bundle's content identity are not
    in it, so config hashes alone cannot establish WHICH bundle (or
    which vintage of it) produced the campaign's canonical baseline.

    Two things are done here, and the limit of each is stated in the
    sidecar rather than glossed:

    * the bundle's fetch-integrity identity (content hash, calendar
      span, tail date, instrument count) is recorded;
    * it is CROSS-CHECKED against the exported rows — a bundle whose
      calendar cannot contain the baseline's dates provably did not
      produce them, and refuses.

    The binding is AUTHORITATIVE, not merely calendar-compatible
    (codex #401 r14 — correcting the weaker claim made at r12): each
    fold manifest's ``config_fingerprint`` already folds the run-time
    bundle content identity into its digest (PR-G+I), so recomputing
    that fingerprint with the SUPPLIED bundle's identity and comparing
    it to what the run recorded proves whether this is the bundle the
    run actually read. ``verify_bundle_matches_run`` does that; this
    function records the identity and rules out calendar-incompatible
    bundles up front.
    """
    from src.data._feature_dataset_cache import read_bundle_tag
    from src.data.pit.bundle_integrity import read_bundle_integrity

    bundle_dir = Path(provider_uri)
    if not bundle_dir.is_dir():
        raise PVBaselineError(
            f"--provider-uri {bundle_dir} is not a directory — the "
            "export must record WHICH data bundle produced the "
            "baseline; refusing.")
    integrity = read_bundle_integrity(bundle_dir)
    if integrity is None or integrity.identity is None:
        raise PVBaselineError(
            f"bundle {bundle_dir} carries no fetch-integrity identity "
            "stamp — the baseline's data provenance cannot be "
            "established; refusing.")
    ident = integrity.identity
    first, last = str(wide_index.min())[:10], str(wide_index.max())[:10]
    if not (ident.calendar_start <= first and last <= ident.calendar_end):
        raise PVBaselineError(
            f"bundle calendar {ident.calendar_start}.."
            f"{ident.calendar_end} cannot contain the exported rows "
            f"{first}..{last} — this bundle did not produce these "
            "predictions; refusing.")
    # The engine fingerprints with read_bundle_tag()'s value
    # ("<tail_date>@<content_hash>", hash RECOMPUTED from the live
    # calendar), NOT the stamp's stored content_hash (codex #401 r15).
    # Call the same function so the comparison is apples-to-apples;
    # deriving the tag by hand here is exactly how the previous
    # revision would have refused every legitimate export.
    tag = read_bundle_tag(str(bundle_dir))
    if not tag or tag == "unknown":
        raise PVBaselineError(
            f"bundle {bundle_dir} yields no content tag "
            f"({tag!r}) — its identity cannot be bound to the run; "
            "refusing.")
    if "_calendar_unreadable_" in tag:
        # read_bundle_tag emits a per-call RANDOM sentinel when it
        # cannot recompute the content hash from the live calendar.
        # Such a tag can never match anything and, more importantly,
        # means the bundle's bytes are unverifiable — refuse instead
        # of comparing against a value that is different every call.
        raise PVBaselineError(
            f"bundle {bundle_dir} has an unreadable calendar "
            "(content hash cannot be recomputed) — its identity is "
            "unverifiable; refusing.")
    return {
        "provider_uri": str(bundle_dir),
        "bundle_tag": tag,
        "content_hash": ident.content_hash,
        "tail_date": ident.tail_date,
        "calendar_start": ident.calendar_start,
        "calendar_end": ident.calendar_end,
        "instrument_count": ident.instrument_count,
        "built_from_holey_fetch": integrity.built_from_holey_fetch,
        "binding_strength": (
            "observed_at_export_time; the walk-forward report carries "
            "no bundle identity, so this records the bundle present "
            "when the export ran and verifies its calendar can "
            "contain the exported rows"
        ),
    }


def verify_bundle_matches_run(run_dir: Path, preset_path: Path,
                              bundle: dict[str, Any],
                              fold_indices: list[int] | None = None) -> str:
    """Prove the supplied bundle is the one the RUN read.

    Each fold manifest stores ``config_fingerprint``, which folds the
    run-time bundle content identity into its digest (PR-G+I). Rebuild
    that fingerprint from the materialized config plus the SUPPLIED
    bundle's identity: equality means this bundle produced the run;
    inequality means a different bundle (or vintage) did, however
    calendar-compatible it looks (codex #401 r14).

    A run whose bundle carried no identity stamp leaves the digest
    unchanged by design ("unknown" is not folded in), so the check
    cannot bind there — and that case already refused earlier, because
    a stamp-less bundle is rejected outright.
    """
    from src.core.walk_forward._resume import compute_config_fingerprint
    from src.core.walk_forward.config import WalkForwardConfig

    manifests = sorted(run_dir.glob("fold_*_manifest.json"))
    if not manifests:
        raise PVBaselineError(
            f"no fold manifests under {run_dir} — the run-time bundle "
            "binding cannot be verified; refusing.")
    payloads = [json.loads(m.read_text(encoding="utf-8"))
                for m in manifests]
    # EVERY exported fold needs its own manifest (codex #401 r15): a
    # torn/copied directory keeping one stale manifest from bundle A
    # alongside reports produced under bundle B would otherwise get
    # the whole export labelled "verified" from that single survivor.
    if fold_indices is not None:
        have = sorted(int(p.get("fold_index", -1)) for p in payloads)
        want = sorted(fold_indices)
        if have != want:
            raise PVBaselineError(
                f"fold manifests cover {have} but the aggregate "
                f"declares folds {want} — an incomplete/mixed run "
                "directory cannot establish the run-time bundle for "
                "every exported fold; refusing.")
    recorded = {p.get("config_fingerprint") for p in payloads}
    if len(recorded) != 1 or None in recorded:
        raise PVBaselineError(
            f"fold manifests disagree on config_fingerprint ({recorded}) "
            "— the folds were not produced by one configuration; "
            "refusing.")
    raw = materialize_preset(preset_path)
    valid = {f.name for f in fields(WalkForwardConfig)}
    expected = compute_config_fingerprint(
        WalkForwardConfig(**{k: v for k, v in raw.items() if k in valid}),
        bundle_identity=bundle["bundle_tag"],
    )
    actual = str(recorded.pop())
    if expected != actual:
        raise PVBaselineError(
            f"the run recorded config_fingerprint={actual} but this "
            f"config + the supplied bundle "
            f"({bundle['bundle_tag'][:28]}…) fingerprints to "
            f"{expected} — the supplied --provider-uri is NOT the "
            "bundle this run read (or the config differs); refusing.")
    return actual


def materialize_preset(preset_path: Path) -> dict[str, Any]:
    """Fully materialize the preset into walk-forward config VALUES.

    Resolving ``extends`` is still not enough (codex #401 r10):
    prediction-driving fields that neither YAML declares — e.g.
    ``label_horizon_days`` — come from ``WalkForwardConfig``'s
    dataclass defaults, yet the engine captures them in the run
    report. A run using a non-default value for such a field would
    otherwise pass the binding check and be exported as the frozen
    baseline. Building the SAME dataclass the engine builds gives the
    complete value set to compare against.
    """
    from dataclasses import asdict

    from src.core._yaml_loader import load_yaml_with_inheritance
    from src.core.walk_forward.config import WalkForwardConfig

    raw = load_yaml_with_inheritance(preset_path)
    if not isinstance(raw, dict):
        raise PVBaselineError(
            f"run config {preset_path} is not a mapping; refusing.")
    valid = {f.name for f in fields(WalkForwardConfig)}
    return asdict(WalkForwardConfig(
        **{k: v for k, v in raw.items() if k in valid}))


def resolve_preset(preset_path: Path) -> dict[str, Any]:
    """Merge a preset with everything it inherits via ``extends``.

    Comparing only the child's declared keys is not enough (codex #401
    r6): a sibling preset such as ``csi800_campaign_base.yaml``
    declares the same overlapping keys but omits ``overall_end``, so
    the check passed while that preset would in fact inherit
    ``2025-12-31`` — the sidecar would then name the wrong config and
    a later provenance dispute would blame the wrong file. Parents
    are merged first, the child overrides.
    """
    chain: list[dict[str, Any]] = []
    seen: set[Path] = set()
    current = preset_path.resolve()
    while True:
        if current in seen:
            raise PVBaselineError(
                f"config extends chain cycles at {current}; refusing.")
        seen.add(current)
        if not current.is_file():
            raise PVBaselineError(
                f"config chain references {current} which does not "
                "exist; refusing.")
        doc = yaml.safe_load(current.read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            raise PVBaselineError(
                f"config {current} is not a mapping; refusing.")
        chain.append(doc)
        parent = doc.get("extends")
        if not parent:
            break
        current = (current.parent / str(parent)).resolve()
    merged: dict[str, Any] = {}
    for doc in reversed(chain):            # parents first
        merged.update({k: v for k, v in doc.items() if k != "extends"})
    return merged


def check_run_config_binding(plan: dict[str, Any], agg: dict[str, Any],
                             preset_path: Path) -> dict[str, Any]:
    """The RUN's own captured config must be the frozen baseline run.

    Hashing the operator-supplied ``--run-config`` proves only that
    the named file is unmodified — not that it drove THIS run (codex
    #401 r4). Without this check a clean run directory produced by a
    different preset (another universe, another tail, single-model
    instead of the frozen warm ensemble) could be exported with a
    sidecar claiming the frozen csi800 Alpha158+LGB baseline and the
    hash of the default preset — certifying the WRONG baseline as the
    campaign's incremental reference, which every downstream
    orthogonality number would then be measured against.

    Compares the aggregate report's embedded ``config`` against both
    the frozen plan and the supplied preset.
    """
    cfg = agg.get("config")
    if not isinstance(cfg, dict):
        raise PVBaselineError(
            "walk_forward_report.json carries no embedded config — "
            "cannot verify which preset produced this run; refusing.")
    base = plan["fitness"]["baseline"]
    expected = {
        "instruments": plan["universe"]["instruments"],
        "overall_end": base["overall_end"],
        "ensemble_window": base["ensemble_window"],
        "feature_handler": "Alpha158",
        "model_type": "LGBModel",
    }
    drift = {
        key: {"run": cfg.get(key), "frozen": want}
        for key, want in expected.items() if cfg.get(key) != want
    }
    if drift:
        raise PVBaselineError(
            f"the run's captured config does not match the frozen "
            f"baseline definition: {drift} — this run directory was "
            "produced by a different preset/universe/tail; exporting "
            "it would certify the WRONG baseline as the campaign's "
            "incremental reference; refusing.")
    # The supplied preset must also be the one that produced the run:
    # its declared keys (it inherits the rest) must agree with the
    # captured config, so the sidecar's run_config_sha256 binds to the
    # file that actually drove these numbers.
    # Full materialization, not just the YAML chain (codex #401 r10):
    # dataclass defaults drive predictions too.
    preset = materialize_preset(preset_path)
    # A materialized field MISSING from the captured config is not a
    # pass (codex #401 r11): the exporter would certify and hash a
    # report that cannot establish which value produced the
    # predictions. Compare on presence first.
    comparable = {k: v for k, v in preset.items()
                  if k not in ("output_dir",)
                  and not (isinstance(v, str) and "${" in v)}
    absent = sorted(k for k in comparable if k not in cfg)
    if absent:
        raise PVBaselineError(
            f"the run's captured config omits materialized field(s) "
            f"{absent} — an older/truncated report cannot establish "
            "which values produced these predictions; re-run the "
            "baseline with the current engine; refusing.")
    preset_drift = {
        key: {"run": cfg.get(key), "preset": val}
        for key, val in preset.items()
        if key in cfg and key not in ("output_dir",)
        # Env-var placeholders (``${QUANT_PROVIDER_URI}``) are expanded
        # at load time; the captured config holds the expansion, so
        # comparing the raw template would false-positive.
        and not (isinstance(val, str) and "${" in val)
        and cfg.get(key) != val
    }
    if preset_drift:
        raise PVBaselineError(
            f"--run-config {preset_path.name} disagrees with the run's "
            f"captured config on {preset_drift} — the supplied preset "
            "did not drive this run; refusing (the sidecar's "
            "run_config_sha256 must bind the config that produced "
            "these numbers).")
    return {k: cfg.get(k) for k in expected}


def check_fold_windows(plan: dict[str, Any],
                       folds: list[dict[str, Any]]) -> dict[str, Any]:
    """Window discipline for the baseline run itself.

    Every fold's TEST window (the out-of-fold rows being exported)
    must lie inside the campaign's IS ∪ OOS span. A fold touching the
    blinded holdout year or the forbidden 2026 period refuses — that
    is the sacred invariant, and a baseline carrying such rows would
    smuggle them into every downstream correlation.
    """
    w = plan["windows"]
    lo, hi = w["is_start"], w["oos_end"]
    offending = [
        f"fold {f['fold_index']}: {f['test_start']}..{f['test_end']}"
        for f in folds
        if not (lo <= f["test_start"] <= hi and lo <= f["test_end"] <= hi)
    ]
    if offending:
        raise PVBaselineError(
            f"fold test windows outside the campaign span {lo}..{hi}: "
            f"{offending} — the blinded {w['holdout_year']} holdout and "
            f"the forbidden period from {w['forbidden_from']} must "
            "never enter the baseline; fix the preset's overall_end and "
            "re-run; refusing.")
    return {"span_start": lo, "span_end": hi}


def resolve_fold_reports(run_dir: Path,
                         agg: dict[str, Any]) -> list[tuple[int, Path]]:
    """Resolve the AUTHORITATIVE fold list from the aggregate report.

    Globbing the directory is not evidence (codex #401 r2): a run dir
    missing one declared fold while carrying a stale
    ``fold_99_report.json`` from another run has the same file COUNT,
    so a count check passes and the stray fold is exported — keying
    downstream orthogonality to rows the run never declared. The
    aggregate report's ``folds[]`` is the record of what the run
    actually produced; every entry must exist, its payload's
    ``fold_index`` must match, and nothing outside the list may be
    exported.
    """
    declared = agg.get("folds")
    if not isinstance(declared, list) or not declared:
        raise PVBaselineError(
            "walk_forward_report.json carries no folds[] list — cannot "
            "establish which folds this run produced; refusing.")
    indices: list[int] = []
    for entry in declared:
        if not isinstance(entry, dict) or "fold_index" not in entry:
            raise PVBaselineError(
                f"malformed folds[] entry {entry!r}; refusing.")
        indices.append(int(entry["fold_index"]))
    # The engine emits CONTIGUOUS 0..N-1 fold indexes, so a declared
    # set that is merely unique is not enough (codex #401 r3): an
    # aggregate saying num_folds=2 while declaring [0, 9] would let a
    # stale fold stand in for a missing fold 1 with every count check
    # still passing. Require the exact expected set.
    expected = list(range(len(declared)))
    if sorted(indices) != expected:
        raise PVBaselineError(
            f"aggregate report declares fold indexes {sorted(indices)} "
            f"but a complete run emits {expected} — torn or "
            "hand-edited aggregate report; refusing.")
    num_folds = agg.get("num_folds")
    if isinstance(num_folds, int) and num_folds != len(declared):
        raise PVBaselineError(
            f"aggregate report declares num_folds={num_folds} but "
            f"folds[] has {len(declared)} entries — inconsistent "
            "aggregate report; refusing.")

    resolved: list[tuple[int, Path]] = []
    run_root = run_dir.resolve()
    for entry, idx in zip(declared, indices, strict=True):
        declared_path = entry.get("report_path")
        # A FAILED fold is recorded with report_path: null (codex #401
        # r10). Falling back to the canonical filename would resolve a
        # same-index report left over from an earlier run: its own sha
        # is self-consistent, so the stale fold would be exported under
        # THIS aggregate's commit and config while the authoritative
        # record says the fold failed. A declared fold must declare a
        # path.
        if not (isinstance(declared_path, str) and declared_path.strip()):
            raise PVBaselineError(
                f"aggregate report declares fold {idx} with an empty "
                f"report_path ({declared_path!r}) — the engine records "
                "a FAILED fold that way; a complete baseline run has "
                "no failed folds, and substituting a local file would "
                "export another run's artifact; refusing.")
        basename = Path(declared_path).name
        # Resolve INSIDE --run-dir first (codex #401 r3): a stored
        # absolute path from the original run must never let the
        # exporter certify one directory while reading fold windows
        # and prediction hashes from another. A wholesale-moved run
        # resolves here; a torn aggregate pointing at a foreign report
        # falls through to the escape check below.
        local = run_dir / basename
        if local.is_file():
            resolved.append((idx, local))
            continue
        if isinstance(declared_path, str) and declared_path:
            outside = Path(declared_path)
            if outside.is_file() and not outside.resolve().is_relative_to(
                    run_root):
                raise PVBaselineError(
                    f"fold {idx}: {local.name} is absent from "
                    f"--run-dir but the declared report_path "
                    f"{declared_path} resolves OUTSIDE it — refusing "
                    "to borrow fold evidence from another directory.")
        raise PVBaselineError(
            f"aggregate report declares fold {idx} but {local} does "
            "not exist — incomplete run directory; refusing.")
    # Anything on disk beyond the declared set is a leftover from
    # another run: refuse rather than quietly ignore it, because the
    # same directory is the evidence base for the ledger entry.
    on_disk = {int(p.name.split("_")[1])
               for p in run_dir.glob("fold_*_report.json")}
    stray = sorted(on_disk - set(indices))
    if stray:
        raise PVBaselineError(
            f"run directory carries fold reports {stray} that the "
            "aggregate report does not declare — leftovers from another "
            "run; export from a clean directory; refusing.")
    return resolved


def _load_fold(run_dir: Path, report_path: Path,
               expected_index: int) -> dict[str, Any]:
    """Read one fold's predictions, verifying the pickle against the
    sha256 the fold report recorded at write time."""
    report = json.loads(report_path.read_text(encoding="utf-8"))
    fold_index = int(report["fold_index"])
    if fold_index != expected_index:
        raise PVBaselineError(
            f"{report_path.name} carries fold_index={fold_index} but the "
            f"aggregate report declares {expected_index} at this slot — "
            "mismatched/stale fold report; refusing.")
    ens = report.get("ensemble") or {}
    declared_sha = ens.get("prediction_artifact_sha256")
    if not isinstance(declared_sha, str) or not re.fullmatch(
            r"[0-9a-f]{64}", declared_sha):
        raise PVBaselineError(
            f"fold {fold_index}: report carries no valid "
            f"prediction_artifact_sha256 ({declared_sha!r}) — cannot "
            "verify the pickle; refusing.")
    pred_path = run_dir / f"fold_{fold_index:02d}_predictions.pkl"
    if not pred_path.is_file():
        raise PVBaselineError(
            f"fold {fold_index}: missing {pred_path.name}; refusing.")
    raw = pred_path.read_bytes()
    actual_sha = hashlib.sha256(raw).hexdigest()
    if actual_sha != declared_sha:
        raise PVBaselineError(
            f"fold {fold_index}: {pred_path.name} digests to "
            f"{actual_sha[:12]}… but the report recorded "
            f"{declared_sha[:12]}… — the artifact changed after the "
            "run; refusing.")
    scores = pickle.loads(raw)
    if not isinstance(scores, pd.Series) or scores.empty:
        raise PVBaselineError(
            f"fold {fold_index}: predictions are not a non-empty "
            f"Series ({type(scores).__name__}); refusing.")
    if list(scores.index.names) != ["datetime", "instrument"]:
        raise PVBaselineError(
            f"fold {fold_index}: unexpected index names "
            f"{list(scores.index.names)}; refusing.")
    windows = report["windows"]
    test_start = str(windows["test"]["start"])[:10]
    test_end = str(windows["test"]["end"])[:10]
    # Trust the ROWS, not only the declaration (codex #401 r1): a
    # report can declare an in-range test window while the pickle
    # carries extra rows — those would pass the sha check (it verifies
    # the file is unmodified since the run, not that the run's
    # geometry was right) and land blinded/forbidden predictions in
    # the baseline. Verify every exported date against the fold's own
    # declared window.
    row_dates = pd.to_datetime(
        scores.index.get_level_values("datetime")).normalize()
    lo, hi = pd.Timestamp(test_start), pd.Timestamp(test_end)
    stray = sorted({str(d)[:10] for d in row_dates[(row_dates < lo)
                                                   | (row_dates > hi)]})
    if stray:
        raise PVBaselineError(
            f"fold {fold_index}: prediction rows dated {stray[:3]} fall "
            f"outside the fold's own test window {test_start}.."
            f"{test_end} — the artifact's geometry disagrees with its "
            "report; refusing (a stray blinded/forbidden row must "
            "never reach the baseline).")
    return {
        "fold_index": fold_index,
        "scores": scores,
        "test_start": test_start,
        "test_end": test_end,
        "ensemble_window": ens.get("window"),
        "prediction_artifact_sha256": declared_sha,
    }


def check_ensemble_semantics(plan: dict[str, Any],
                             folds: list[dict[str, Any]]) -> int:
    """Every fold must carry the FROZEN ensemble window (decision ②:
    the baseline is the production-equivalent warm ensemble, not a
    single-model output)."""
    frozen = int(plan["fitness"]["baseline"]["ensemble_window"])
    mismatched = [
        f"fold {f['fold_index']}: {f['ensemble_window']!r}"
        for f in folds if f["ensemble_window"] != frozen
    ]
    if mismatched:
        raise PVBaselineError(
            f"ensemble window must be the frozen {frozen} on every "
            f"fold (decision ②: production-equivalent warm ensemble); "
            f"got {mismatched}; refusing.")
    return frozen


def assemble_wide(folds: list[dict[str, Any]]) -> pd.DataFrame:
    """Concatenate per-fold out-of-fold predictions into one wide
    (date × instrument) frame.

    Fold test windows are disjoint by construction (step == test
    months), so a repeated (date, instrument) pair means overlapping
    folds or a duplicated directory — refuse rather than let one fold
    silently overwrite another.
    """
    stacked = pd.concat([f["scores"] for f in folds])
    dupes = stacked.index[stacked.index.duplicated()]
    if len(dupes) > 0:
        raise PVBaselineError(
            f"{len(dupes)} duplicated (date, instrument) rows across "
            f"folds (e.g. {list(dupes[:3])}) — overlapping folds or a "
            "mixed run directory; refusing.")
    wide = stacked.unstack("instrument").sort_index()
    wide.index = pd.to_datetime(wide.index)
    if wide.empty:
        raise PVBaselineError("assembled baseline frame is empty.")
    return wide


def build_sidecar(plan: dict[str, Any], *, wide: pd.DataFrame,
                  file_sha256: str, run_config_rel: str,
                  run_config_sha256: str, provenance: dict[str, Any],
                  ensemble_window: int, run_dir: str,
                  folds: list[dict[str, Any]],
                  run_identity: dict[str, Any],
                  resolved_config: dict[str, Any],
                  config_chain: dict[str, str],
                  bundle: dict[str, Any]) -> dict[str, Any]:
    """The provenance sidecar the miner and the OOS evaluator verify.

    ``model`` / ``file_sha256`` / ``run_config_sha256`` / ``source_git``
    are the four fields both consumers require; the rest is disclosure
    — notably the IS coverage gap that operator decision A accepted
    (the baseline keeps the production fold geometry, so its first
    out-of-fold date is later than the IS window start).
    """
    w = plan["windows"]
    is_days = int(((wide.index >= w["is_start"])
                   & (wide.index <= w["is_end"])).sum())
    oos_days = int(((wide.index >= w["oos_start"])
                    & (wide.index <= w["oos_end"])).sum())
    return {
        "protocol_id": PROTOCOL_ID,
        "model": plan["fitness"]["baseline"]["model"],
        "file_sha256": file_sha256,
        "run_config": run_config_rel,
        # sha256 of the RESOLVED config the engine captured — the
        # settings that actually produced these predictions, parents
        # included (codex #401 r5). Consumers requiring
        # ``run_config_sha256`` bind to this, not to a child preset
        # whose inherited fields were never sealed.
        "run_config_sha256": run_config_sha256,
        "run_config_sha256_kind": "resolved_walk_forward_config",
        # Full snapshot + per-file chain hashes so a later disagreement
        # names WHAT changed, not merely THAT something did.
        "resolved_config": resolved_config,
        "config_chain_sha256": config_chain,
        # WHICH data produced this (codex #401 r12) — config hashes
        # alone cannot say. Carries its own binding-strength note.
        "data_bundle": bundle,
        "source_git": provenance["source_git"],
        "source_git_dirty": provenance["source_git_dirty"],
        "walk_forward_run_dir": run_dir,
        # The run's OWN captured config values, verified equal to the
        # frozen baseline definition (codex #401 r4) — evidence, not a
        # restatement of what the operator claimed on the CLI.
        "run_config_captured": run_identity,
        "ensemble_window": ensemble_window,
        "n_folds": len(folds),
        "fold_prediction_sha256": {
            str(f["fold_index"]): f["prediction_artifact_sha256"]
            for f in folds
        },
        "coverage": {
            # Decision A disclosure: the IS window starts before the
            # first out-of-fold date, so IS coverage is partial BY
            # DESIGN — recorded here so no reader mistakes it for a
            # data defect or assumes full-window orthogonality.
            "is_window": [w["is_start"], w["is_end"]],
            "oos_window": [w["oos_start"], w["oos_end"]],
            "first_baseline_date": str(wide.index.min())[:10],
            "last_baseline_date": str(wide.index.max())[:10],
            "is_days_covered": is_days,
            "oos_days_covered": oos_days,
            "is_coverage_policy":
                plan["fitness"]["orthogonality"]["is_coverage_policy"],
        },
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run-dir", required=True,
                   help="Completed walk-forward run directory.")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--run-config",
                   default="config/presets/pv_incremental_baseline.yaml",
                   help="Repo-relative preset that drove the run.")
    p.add_argument("--provider-uri",
                   default="${QUANT_PROVIDER_URI}",
                   help="PIT bundle the run read; its fetch-integrity "
                        "identity is recorded and cross-checked.")
    args = p.parse_args(argv)

    try:
        plan = load_frozen_plan()
        run_dir = Path(args.run_dir)
        agg_path = run_dir / "walk_forward_report.json"
        if not agg_path.is_file():
            raise PVBaselineError(
                f"{agg_path} not found — point --run-dir at a COMPLETED "
                "walk-forward run; refusing.")
        agg = json.loads(agg_path.read_text(encoding="utf-8"))
        provenance = check_run_provenance(agg)
        config_path = _REPO_ROOT / args.run_config
        if not config_path.is_file():
            raise PVBaselineError(
                f"run config {config_path} not found; refusing.")
        # Bind the run to the FROZEN baseline definition and to the
        # supplied preset before anything else is read (codex #401 r4).
        run_identity = check_run_config_binding(plan, agg, config_path)

        # Authoritative fold list from the aggregate report — never
        # the directory listing (codex #401 r2).
        resolved = resolve_fold_reports(run_dir, agg)
        folds = [_load_fold(run_dir, rp, idx) for idx, rp in resolved]
        ensemble_window = check_ensemble_semantics(plan, folds)
        check_fold_windows(plan, folds)
        wide = assemble_wide(folds)
        # Defence in depth after assembly: per-fold row checks already
        # ran, so anything out of span here means a fold declared a
        # window outside the campaign that slipped both gates. Refuse
        # before writing a single byte.
        w = plan["windows"]
        span_lo, span_hi = pd.Timestamp(w["is_start"]), pd.Timestamp(
            w["oos_end"])
        out_of_span = sorted(
            {str(d)[:10] for d in wide.index
             if d < span_lo or d > span_hi})
        if out_of_span:
            raise PVBaselineError(
                f"assembled baseline carries rows outside "
                f"{w['is_start']}..{w['oos_end']} (e.g. "
                f"{out_of_span[:3]}) — blinded holdout / forbidden "
                "period rows must never be exported; refusing.")

        # Seal the RESOLVED config (the engine's own capture) plus the
        # whole extends chain — not just the child preset's bytes
        # (codex #401 r5).
        run_config_sha = resolved_config_sha256(agg["config"])
        config_chain = config_chain_sha256(config_path)

        provider_uri = args.provider_uri
        if provider_uri.startswith("${"):
            import os
            provider_uri = os.environ.get(
                "QUANT_PROVIDER_URI", "D:/qlib_data/my_cn_data_pit")
        # ALL validation before the first byte is written (codex #401
        # r13): a refusal after the parquet exists leaves an orphan
        # without its provenance sidecar, and the corrected retry then
        # refuses because the export "already exists" — a recoverable
        # config error turned into a manual-cleanup deadlock.
        bundle = bind_source_bundle(provider_uri, wide.index)
        bundle["run_config_fingerprint"] = verify_bundle_matches_run(
            run_dir, config_path, bundle,
            fold_indices=[f["fold_index"] for f in folds])
        bundle["binding_strength"] = (
            "verified_against_run: the fold manifests' "
            "config_fingerprint folds the run-time bundle identity, "
            "and it matches this config + this bundle"
        )

        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_parquet = out_dir / "baseline_preds.parquet"
        if out_parquet.exists():
            raise PVBaselineError(
                f"{out_parquet} already exists — exports never "
                "overwrite (a silently replaced baseline would "
                "invalidate every score keyed to the old one); use a "
                "fresh --out-dir; refusing.")
        wide.to_parquet(out_parquet)
        file_sha = hashlib.sha256(out_parquet.read_bytes()).hexdigest()

        sidecar = build_sidecar(
            plan, wide=wide, file_sha256=file_sha,
            run_config_rel=args.run_config,
            run_config_sha256=run_config_sha, provenance=provenance,
            ensemble_window=ensemble_window, run_dir=str(run_dir),
            folds=folds, run_identity=run_identity,
            resolved_config=agg["config"], config_chain=config_chain,
            bundle=bundle)
        (out_dir / "baseline_preds.parquet.provenance.json").write_text(
            json.dumps(sidecar, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")

        cov = sidecar["coverage"]
        print(f"[pv-baseline] exported {out_parquet}")
        print(f"[pv-baseline] shape={wide.shape} folds={len(folds)} "
              f"ensemble_window={ensemble_window}")
        print(f"[pv-baseline] span {cov['first_baseline_date']}.."
              f"{cov['last_baseline_date']} | IS days covered="
              f"{cov['is_days_covered']} OOS days covered="
              f"{cov['oos_days_covered']}")
        print(f"[pv-baseline] file_sha256={file_sha}")
        print("[pv-baseline] register BOTH the intent and this result "
              "in docs/prereg/pv_incremental_ledger.yaml before the "
              "baseline keys any GP run.")
    except PVBaselineError as exc:
        print(f"[pv-baseline] REFUSED: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
