from __future__ import annotations

import hashlib
import json
import pickle
from pathlib import Path
from typing import Any, Sequence

from src.core.logger import get_logger

_logger = get_logger(__name__)


def apply_ensemble(
    *,
    current_predictions: Any,
    current_dataset: Any,
    prior_model_paths: Sequence[Any],
    ensemble_window: int,
    current_fold_index: int,
) -> tuple[Any, dict[str, Any]]:
    """Average current fold's predictions with up to ``N-1`` prior fold models.

    Returns ``(predictions, meta)``:

    - ``predictions``: a ``pd.Series`` aligned to ``current_predictions``'
      ``(datetime, instrument)`` index. When the window is ``1`` or no
      priors are available, this is exactly ``current_predictions``
      (same object — no copy).
    - ``meta``: a ``dict`` describing what actually happened, embedded
      in the per-fold report. Keys:

      * ``window`` — the configured ``ensemble_window``.
      * ``used`` — ``True`` iff ≥1 prior model contributed.
      * ``n_models`` — number of models averaged (current + priors).
      * ``contributing_folds`` — fold indices whose models were
        averaged in, in chronological order, with the current fold
        last.
      * ``prior_models_attempted`` — how many prior pickle paths the
        engine tried to load (``ensemble_window - 1`` capped by the
        available history).
      * ``prior_models_loaded`` — how many of those actually
        predicted successfully. A gap (e.g. corrupted pickle, model
        schema mismatch) is logged and skipped, not raised, so a
        single broken artifact does not abort the whole run.

    Why this is "warm" rather than strict
    --------------------------------------
    Each prior model was trained against its own fold's processed
    dataset (RobustZScoreNorm fit on that train window's stats); we
    run it against the *current* fold's dataset, which has different
    normalisation parameters. In practice cross-section IC is robust
    to these small distribution shifts on A-share data; in pathological
    regimes the operator should fall back to ``ensemble_window=1``
    and inspect the per-fold reports.
    """
    meta: dict[str, Any] = {
        "window": int(ensemble_window),
        "used": False,
        "n_models": 1,
        "contributing_folds": [int(current_fold_index)],
        "contributing_model_refs": [],
        "prior_models_attempted": 0,
        "prior_models_loaded": 0,
        "prior_models_index_mismatched": 0,
        "rejected_priors": [],
    }

    # No-op fast path: window 1 means "current fold only", which is
    # the legacy behaviour. Returning the current Series unchanged
    # also keeps ``ensemble_window=1`` cheap (no copy, no I/O).
    if ensemble_window <= 1:
        return current_predictions, meta

    # Window asks for priors but none exist yet (fold 0, or fold N
    # where prior pickles were not provided). Natural degradation:
    # use whatever's available — which here is just the current
    # model. Same shape as fold 0 below.
    if not prior_model_paths:
        return current_predictions, meta

    import pandas as pd

    # Pick the most-recent ``window - 1`` priors. ``prior_model_paths``
    # is in chronological (oldest-first) order; ``[-(window-1):]``
    # gives the newest ones.
    priors_to_load = list(prior_model_paths[-(ensemble_window - 1):])
    meta["prior_models_attempted"] = len(priors_to_load)

    # Stack predictions: start with the current fold's series, then
    # append each prior model's prediction over the same dataset. We
    # require exact index equality before stacking so pandas never
    # union-aligns a stale prior into a different signal universe.
    prediction_frames: list[Any] = [current_predictions.rename("m0")]
    contributing_folds: list[int] = []
    loaded = 0

    for offset, prior_ref in enumerate(priors_to_load):
        if (
            isinstance(prior_ref, tuple)
            and len(prior_ref) == 2
        ):
            prior_fold_idx, prior_path = prior_ref
        else:
            # Backward-compatible fallback for tests/direct callers that
            # still pass bare paths. Runtime ``run`` passes real refs.
            prior_fold_idx = current_fold_index - len(priors_to_load) + offset
            prior_path = prior_ref
        prior_path = str(prior_path)
        # ── provenance sidecar check ─────────────────────────
        # Read the model's provenance sidecar (written by
        # ModelTrainer.train_and_predict) before unpickling.
        # A lightgbm minor-bump can silently change booster
        # serialisation semantics — the same pickle may load
        # without error but produce semantically different
        # behaviour. We guard against that here by comparing
        # library versions.
        skip_prior = False
        sidecar_path = Path(prior_path).with_suffix(".pkl.meta.json")
        if sidecar_path.is_file():
            try:
                sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
                # Check pickle integrity against the sidecar hash.
                pkl_sha = sidecar.get("pkl_sha256")
                if pkl_sha:
                    actual_sha = hashlib.sha256(
                        Path(prior_path).read_bytes()
                    ).hexdigest()
                    if actual_sha != pkl_sha:
                        _logger.warning(
                            "Fold %d ensemble: prior model %r sha256 "
                            "mismatch (expected %s, got %s) — pickle "
                            "replaced or corrupt. Skipping.",
                            current_fold_index, prior_path,
                            pkl_sha, actual_sha,
                        )
                        skip_prior = True
                        meta["rejected_priors"].append({
                            "fold_idx": prior_fold_idx,
                            "path": prior_path,
                            "reason": f"pkl_sha256 mismatch",
                        })
                sidecar_lgb = sidecar.get("lightgbm_version")
                if sidecar_lgb:
                    import lightgbm as _lgb
                    if sidecar_lgb != _lgb.__version__:
                        _logger.warning(
                            "Fold %d ensemble: prior model %r trained with "
                            "lightgbm %s; current is %s — skipping.",
                            current_fold_index, prior_path,
                            sidecar_lgb, _lgb.__version__,
                        )
                        skip_prior = True
                        meta["rejected_priors"].append({
                            "fold_idx": prior_fold_idx,
                            "path": prior_path,
                            "reason": f"lightgbm {sidecar_lgb} != {_lgb.__version__}",
                        })
            except Exception as exc:  # noqa: BLE001
                _logger.warning(
                    "Fold %d ensemble: sidecar parse/check failed "
                    "for %r (%s: %s) — loading prior model without "
                    "version guard.",
                    current_fold_index, prior_path,
                    type(exc).__name__, exc,
                )
        if skip_prior:
            continue

        try:
            with open(prior_path, "rb") as f:
                prior_model = pickle.load(f)
            # Each qlib LGBModel has ``predict(dataset, segment)``.
            # We want the test-segment scores aligned to the current
            # dataset's test slice — same as what the current model
            # produced — so use the same segment name the trainer
            # writes (``"test"``).
            prior_pred = prior_model.predict(current_dataset, "test")
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "Fold %d ensemble: skipping prior model %r — load/"
                "predict failed (%s: %s). Continuing with the "
                "remaining priors so a single bad pickle does not "
                "abort the run.",
                current_fold_index, prior_path, type(exc).__name__, exc,
            )
            continue

        # Coerce to Series. qlib's predict returns a Series for the
        # canonical handler, but a research-time monkey-patch could
        # in principle return a DataFrame; reject that loudly.
        if not isinstance(prior_pred, pd.Series):
            _logger.warning(
                "Fold %d ensemble: prior model %r returned %s, "
                "expected pd.Series. Skipping this prior.",
                current_fold_index, prior_path,
                type(prior_pred).__name__,
            )
            continue
        if not prior_pred.index.equals(current_predictions.index):
            meta["prior_models_index_mismatched"] = int(
                meta["prior_models_index_mismatched"]
            ) + 1
            meta["rejected_priors"].append({
                "fold_index": int(prior_fold_idx),
                "model_ref": prior_path,
                "reason": "index_mismatch",
                "current_size": int(len(current_predictions.index)),
                "prior_size": int(len(prior_pred.index)),
            })
            _logger.warning(
                "Fold %d ensemble: prior model %r returned an index that "
                "does not exactly match current predictions; skipping it "
                "to avoid pandas union-alignment changing the signal "
                "universe.",
                current_fold_index, prior_path,
            )
            continue

        prediction_frames.append(
            prior_pred.rename(f"m{offset + 1}")
        )
        contributing_folds.append(int(prior_fold_idx))
        meta["contributing_model_refs"].append({
            "fold_index": int(prior_fold_idx),
            "model_ref": prior_path,
        })
        loaded += 1

    meta["prior_models_loaded"] = loaded

    if loaded == 0:
        # Every prior failed — fall back to current-fold-only. The
        # warning was already emitted per-prior above; here we just
        # surface the aggregate state in the meta block.
        return current_predictions, meta

    # ``concat(axis=1)`` aligns each model's predictions on the
    # ``(datetime, instrument)`` index. ``mean(axis=1, skipna=True)``
    # then averages across models — ``skipna`` matters because a
    # prior model can legitimately have NaN scores for instruments
    # not in its training universe (e.g. newly listed names),
    # whereas the current model has them.
    stacked = pd.concat(prediction_frames, axis=1)
    averaged = stacked.mean(axis=1, skipna=True)
    averaged = averaged.reindex(current_predictions.index)
    # The result Series has no name; rename to match the current
    # predictions' name so downstream consumers (SignalAnalyzer,
    # BacktestRunner) see the same shape.
    averaged.name = getattr(current_predictions, "name", None)

    # Order contributing_folds chronologically with current fold last
    # so the reader sees "earliest -> latest -> current" — matches
    # the semantic of "warm ensemble".
    meta["used"] = True
    meta["n_models"] = 1 + loaded
    meta["contributing_folds"] = contributing_folds + [int(current_fold_index)]

    _logger.info(
        "Fold %d ensemble: averaged %d models (current + %d priors, "
        "contributing folds %s).",
        current_fold_index, meta["n_models"], loaded,
        meta["contributing_folds"],
    )

    return averaged, meta

def write_prediction_artifact(path: Path, predictions: Any) -> str:
    """Persist the exact prediction Series consumed by official backtest.

    Model pickles alone are insufficient provenance once walk-forward
    ensembling is enabled: the backtest consumes the materialized signal,
    not a single model artifact. Return a SHA256 so reports can identify
    the exact bytes written.
    """
    with open(path, "wb") as f:
        pickle.dump(predictions, f)
    return hashlib.sha256(path.read_bytes()).hexdigest()
