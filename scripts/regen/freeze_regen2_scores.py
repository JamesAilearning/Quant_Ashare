"""Freeze the REGEN-2 per-fold prediction Series into a deterministic-replay fixture.

REGEN-2 re-baselined the walk-forward headline by switching the excess benchmark
to the SH000300TR total-return index and retraining on the corrected 2026-06-17
bundle (a fresh GPU walk-forward run; see ``docs/baseline_regen2.md``). To make
that baseline **replay-anchored** (the ``v2-canonical-backtest-contract`` OpenSpec
requirement), this script freezes the run's post-ensemble per-fold prediction
Series — exactly as REGEN-A's ``frozen_fold_scores.pkl.gz`` froze the C1 scores —
so ``replay_frozen_baseline_regen2`` can reproduce the committed REGEN-2 aggregate
to machine precision WITHOUT retraining or the full bundle.

Source: the engine's per-fold artifacts under ``output/walk_forward_regen2_tr/``
(``fold_NN_predictions.pkl`` = the post-ensemble Series; ``fold_NN_report.json``
= the train/valid/test windows). Output is byte-compatible with the loader in
``scripts/regen/replay_frozen_baseline.py`` (``dict[int, {scores, train, valid,
test, prediction_shape}]``, gzip+pickle).

Unlike REGEN-A (22 valid folds + a NaN-tail fold 22 that overran the old bundle),
REGEN-2 has **23 real folds** — fold 22 (2025Q4) completes on the extended bundle.

Usage::

    python scripts/regen/freeze_regen2_scores.py
"""
from __future__ import annotations

import argparse
import gzip
import json
import pickle
from pathlib import Path
from typing import Any

import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

_DEFAULT_RUN_DIR = _PROJECT_ROOT / "output" / "walk_forward_regen2_tr"
_DEFAULT_OUT = _PROJECT_ROOT / "tests" / "regression" / "fixtures" / "regen2" / "frozen_fold_scores.pkl.gz"
_N_FOLDS = 23  # 0..22, ALL real (fold 22 = 2025Q4 completes on the 2026-06-17 bundle)


def _load_fold(run_dir: Path, fold: int) -> dict[str, Any]:
    pred_path = run_dir / f"fold_{fold:02d}_predictions.pkl"
    report_path = run_dir / f"fold_{fold:02d}_report.json"
    if not pred_path.is_file():
        raise FileNotFoundError(f"missing prediction pickle for fold {fold}: {pred_path}")
    if not report_path.is_file():
        raise FileNotFoundError(f"missing report for fold {fold}: {report_path}")
    with open(pred_path, "rb") as fh:
        scores = pickle.load(fh)
    if not isinstance(scores, pd.Series) or scores.empty:
        raise ValueError(f"fold {fold}: predictions are not a non-empty Series ({type(scores).__name__})")
    if list(scores.index.names) != ["datetime", "instrument"]:
        raise ValueError(f"fold {fold}: unexpected index names {scores.index.names}")
    windows = json.loads(report_path.read_text(encoding="utf-8"))["windows"]
    for key in ("train", "valid", "test"):
        if not (windows.get(key, {}).get("start") and windows[key].get("end")):
            raise ValueError(f"fold {fold}: window {key} missing start/end in {report_path}")
    return {
        "scores": scores,
        "train": {"start": windows["train"]["start"], "end": windows["train"]["end"]},
        "valid": {"start": windows["valid"]["start"], "end": windows["valid"]["end"]},
        "test": {"start": windows["test"]["start"], "end": windows["test"]["end"]},
        "prediction_shape": [int(len(scores))],
    }


def freeze(run_dir: Path, out_path: Path) -> dict[int, dict[str, Any]]:
    frozen: dict[int, dict[str, Any]] = {}
    for fold in range(_N_FOLDS):
        frozen[fold] = _load_fold(run_dir, fold)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # protocol 4 (py3.8+ default is 5; pin 4 for broad readability, matching the
    # REGEN-A fixture's gzip+pickle envelope the loader already reads).
    with gzip.open(out_path, "wb") as fh:
        pickle.dump(frozen, fh, protocol=4)
    return frozen


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", default=str(_DEFAULT_RUN_DIR),
                    help="walk-forward output dir holding fold_NN_predictions.pkl + fold_NN_report.json")
    ap.add_argument("--out", default=str(_DEFAULT_OUT))
    args = ap.parse_args(argv)

    frozen = freeze(Path(args.run_dir), Path(args.out))
    total_rows = sum(len(e["scores"]) for e in frozen.values())
    print(f"froze {len(frozen)} folds ({sorted(frozen)}) — {total_rows} total score rows")
    print(f"  fold 22 test window: {frozen[22]['test']}  ({frozen[22]['prediction_shape'][0]} rows)")
    print(f"written -> {args.out}  ({Path(args.out).stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
