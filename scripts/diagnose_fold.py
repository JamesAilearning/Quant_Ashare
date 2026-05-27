#!/usr/bin/env python
"""Diagnose a single walk-forward fold.

Given a walk-forward run directory and a fold index, this script collects
signals from every available artifact (per-fold report, positions JSON,
model pickle, qlib price data) and prints a structured diagnostic report.

Usage::

    python scripts/diagnose_fold.py output/walk_forward 5
    python scripts/diagnose_fold.py output/walk_forward 0 --output fold0.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    _HAS_MPL = True
except ImportError:
    _HAS_MPL = False


def die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


class FoldDiagnosis:
    def __init__(self, run_dir: Path, fold_index: int) -> None:
        self.run_dir = run_dir
        self.fold_index = fold_index
        self._report: dict[str, Any] | None = None
        self._positions: dict[str, dict[str, float]] | None = None
        self._model_path: Path | None = None
        self._loaded = False

    def load(self) -> None:
        if self._loaded:
            return
        self._loaded = True

        report_path = self.run_dir / f"fold_{self.fold_index:02d}_report.json"
        if report_path.is_file():
            self._report = json.loads(report_path.read_text(encoding="utf-8"))

        pos_path = self.run_dir / f"fold_{self.fold_index:02d}_positions.json"
        if pos_path.is_file():
            self._positions = json.loads(pos_path.read_text(encoding="utf-8"))

        model_path = self.run_dir / f"model_fold{self.fold_index}.pkl"
        if model_path.is_file():
            self._model_path = model_path

    # ── headline ──────────────────────────────────────────────────

    def headline(self) -> dict[str, Any]:
        r = self._report or {}
        m = r.get("metrics", {})
        w = r.get("windows", {})
        test = w.get("test", {})
        test_period = f"{test.get('start', '?')} ~ {test.get('end', '?')}"
        return {
            "fold_index": self.fold_index,
            "test_period": test_period,
            "ic_1d": m.get("ic_1d"),
            "ic_5d": m.get("ic_5d"),
            "annualized_return": m.get("annualized_return"),
            "max_drawdown": m.get("max_drawdown"),
            "information_ratio": m.get("information_ratio"),
        }

    # ── daily IC time series ──────────────────────────────────────

    def ic_decay_curve(self) -> list[float]:
        r = self._report or {}
        sig = r.get("signal_analysis", {})
        curve: list[float] = sig.get("ic_decay", [])
        return curve

    # ── sector exposure ───────────────────────────────────────────

    def sector_exposure(self) -> dict[str, Any] | None:
        r = self._report or {}
        return r.get("attribution")

    # ── training diagnostic ───────────────────────────────────────

    def training_diagnostic(self) -> dict[str, Any]:
        r = self._report or {}
        model = r.get("model", {})
        return {
            "artifact_path": model.get("artifact_path"),
            "best_iteration": model.get("best_iteration"),
            "final_valid_loss": model.get("final_valid_loss"),
            "prediction_shape": model.get("prediction_shape"),
        }

    # ── charts ────────────────────────────────────────────────────

    def chart_ic_decay(self, path: Path) -> Path | None:
        if not _HAS_MPL:
            return None
        curve = self.ic_decay_curve()
        if not curve:
            return None
        lags = list(range(1, len(curve) + 1))
        fig, ax = plt.subplots(figsize=(8, 3))
        ax.axhline(y=0, color="gray", linestyle="--", linewidth=0.8)
        ax.bar(lags, curve, color="#2196F3", width=0.6)
        ax.set_title(f"Fold {self.fold_index} IC Decay Curve")
        ax.set_xlabel("Lag (days)")
        ax.set_ylabel("Mean IC")
        ax.set_xticks(lags)
        fig.tight_layout()
        fig.savefig(path, dpi=120)
        plt.close(fig)
        return path


def format_section(title: str, suffix: str = "") -> str:
    bar = "─" * (60 - len(title))
    return f"\n\N{box drawings light horizontal}{title} {bar}{suffix}\n"


def diagnose(run_dir: Path, fold_index: int, output_dir: Path | None = None) -> str:
    diag = FoldDiagnosis(run_dir, fold_index)
    diag.load()
    h = diag.headline()

    lines: list[str] = []

    # ── [1] Headline ────────────────────────────────────────────
    lines.append(format_section("Fold Diagnosis", f"fold {fold_index}"))
    lines.append(f"Run dir : {run_dir}\n")
    lines.append(f"Test period : {h['test_period']}")
    lines.append(f"IC(1d)      : {h['ic_1d']!r}")
    lines.append(f"IC(5d)      : {h['ic_5d']!r}")
    lines.append(f"Return      : {h['annualized_return']!r}")
    lines.append(f"Max DD      : {h['max_drawdown']!r}")
    lines.append(f"IR          : {h['information_ratio']!r}")

    # ── [2] IC decay curve ──────────────────────────────────────
    curve = diag.ic_decay_curve()
    if curve:
        lines.append(format_section("IC Decay Curve", f"{len(curve)} lags"))
        lines.append(
            " ".join(f"lag{i+1}={v:.4f}" for i, v in enumerate(curve[:10]))
        )
        if len(curve) > 10:
            lines.append(f"  ... +{len(curve)-10} more lags")

    # ── [3] Sector exposure ──────────────────────────────────────
    att = diag.sector_exposure()
    if att:
        lines.append(format_section("Sector Attribution"))
        if isinstance(att, dict):
            se = att.get("selection_effects", {})
            ae = att.get("allocation_effects", {})
            lines.append("Top-5 selection effects:")
            for name, v in sorted(se.items(), key=lambda x: float(x[1] or 0), reverse=True)[:5]:
                lines.append(f"  {name:30s} {float(v):+.4f}")
            lines.append("Top-5 allocation effects:")
            for name, v in sorted(ae.items(), key=lambda x: float(x[1] or 0), reverse=True)[:5]:
                lines.append(f"  {name:30s} {float(v):+.4f}")

    # ── [4] Training diagnostic ──────────────────────────────────
    td = diag.training_diagnostic()
    if td:
        lines.append(format_section("Training"))
        for k, v in td.items():
            lines.append(f"  {k}: {v}")

    # ── [5] Charts ────────────────────────────────────────────────
    out = output_dir or run_dir
    out.mkdir(parents=True, exist_ok=True)
    chart_path = diag.chart_ic_decay(out / f"fold{fold_index:02d}_ic_decay.png")
    if chart_path:
        lines.append(format_section("Charts"))
        lines.append(f"  ic_decay.png: {chart_path}")

    return "".join(line + "\n" for line in lines)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Diagnose a single walk-forward fold.",
    )
    p.add_argument("run_dir", type=Path, help="Walk-forward output directory.")
    p.add_argument("fold_index", type=int, help="Zero-based fold index.")
    p.add_argument(
        "--output", "-o", type=Path, default=None,
        help="Write a Markdown file instead of printing to stdout.",
    )
    p.add_argument(
        "--charts-dir", type=Path, default=None,
        help="Directory for diagnostic charts (default: run_dir).",
    )
    args = p.parse_args(argv)

    if not args.run_dir.is_dir():
        die(f"{args.run_dir} is not a directory.")

    report = diagnose(args.run_dir, args.fold_index, args.charts_dir)

    if args.output:
        args.output.write_text(report, encoding="utf-8")
        print(f"Diagnosis written to {args.output}")
    else:
        print(report)


if __name__ == "__main__":
    main()
