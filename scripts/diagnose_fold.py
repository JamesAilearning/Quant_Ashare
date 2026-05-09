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
import math
import pickle
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

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
        return {
            "fold_index": self.fold_index,
            "test_period": r.get("test_period", "unknown"),
            "ic_1d": r.get("ic_1d"),
            "ic_5d": r.get("ic_5d"),
            "annualized_return": r.get("annualized_return"),
            "max_drawdown": r.get("max_drawdown"),
            "information_ratio": r.get("information_ratio"),
        }

    # ── daily IC time series ──────────────────────────────────────

    def daily_ic(self) -> list[dict[str, Any]]:
        r = self._report or {}
        ic_decay = r.get("ic_decay", {})
        daily = ic_decay.get("lag_1_daily_ic", [])
        if not daily:
            return []
        return [
            {"date": d["date"], "ic": d["value"]}
            for d in daily if d.get("value") is not None
        ]

    def worst_ic_days(self, n: int = 5) -> list[dict[str, Any]]:
        daily = self.daily_ic()
        daily.sort(key=lambda d: d["ic"] or 0.0)
        return daily[:n]

    def best_ic_days(self, n: int = 5) -> list[dict[str, Any]]:
        daily = self.daily_ic()
        daily.sort(key=lambda d: d["ic"] or 0.0, reverse=True)
        return daily[:n]

    # ── sector exposure ───────────────────────────────────────────

    def sector_exposure(self) -> dict[str, Any] | None:
        r = self._report or {}
        return r.get("attribution")

    # ── training diagnostic ───────────────────────────────────────

    def training_diagnostic(self) -> dict[str, Any]:
        if self._model_path is None:
            return {}
        try:
            with open(self._model_path, "rb") as f:
                model = pickle.load(f)
        except Exception:
            return {"error": "failed to load model pickle"}

        inner = getattr(model, "model", None)
        result: dict[str, Any] = {}
        if hasattr(inner, "best_iteration"):
            result["best_iteration"] = int(inner.best_iteration)
        if hasattr(inner, "best_score"):
            bs = inner.best_score
            if "valid" in bs:
                result["best_valid_score"] = float(bs["valid"][-1])
            else:
                result["best_score"] = float(str(bs)[:120])
        return result

    # ── charts ────────────────────────────────────────────────────

    def chart_daily_ic(self, path: Path) -> Path | None:
        if not _HAS_MPL:
            return None
        daily = self.daily_ic()
        if not daily:
            return None
        dates = [d["date"] for d in daily]
        ics = [d["ic"] for d in daily]
        fig, ax = plt.subplots(figsize=(10, 3))
        ax.axhline(y=0, color="gray", linestyle="--", linewidth=0.8)
        ax.plot(dates, ics, marker=".", markersize=3, linewidth=0.8, color="#2196F3")
        ax.set_title(f"Fold {self.fold_index} Daily IC(1d)")
        ax.set_ylabel("IC")
        ax.tick_params(axis="x", rotation=45)
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

    # ── [2] Daily IC time series ─────────────────────────────────
    daily = diag.daily_ic()
    if daily:
        lines.append(format_section("Daily IC", f"{len(daily)} days"))
        worst = diag.worst_ic_days(5)
        lines.append("Worst 5 days:")
        for d in worst:
            lines.append(f"  {d['date']}  IC={d['ic']:.4f}")
        best = diag.best_ic_days(5)
        lines.append("Best 5 days:")
        for d in best:
            lines.append(f"  {d['date']}  IC={d['ic']:.4f}")

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
    chart_path = diag.chart_daily_ic(out / f"fold{fold_index:02d}_daily_ic.png")
    if chart_path:
        lines.append(format_section("Charts"))
        lines.append(f"  daily_ic.png: {chart_path}")

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
