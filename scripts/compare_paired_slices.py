"""Paired NET + GROSS deltas with pre-registered exclusion slices — the
auditable evidence generator behind campaign results docs.

The gated compare CLI (`compare_walk_forward_runs.py`) emits the paired NET
annualized diff/CI and gross-IR diagnostics, but adjudication rules can hinge
on the paired GROSS delta (e.g. the 阶段6 10d escalation rule) and on
pre-registered sensitivity slices — neither of which the CLI emits directly
(codex P2 on #326: an adjudication-critical number must be reproducible from
committed evidence, never from an ad-hoc harness). This script computes both
with the SAME ruler machinery (``src.core.comparison``: shared-day alignment,
acf-decay block length, circular moving-block bootstrap, seed 42,
n_boot 10000) so its output can be committed verbatim next to the verdict.

Usage (repo root; slices are NAME=START..END date ranges to EXCLUDE):

    python scripts/compare_paired_slices.py \
        output/stage6/h1_st_off_baseline output/stage6/h5_st_off_treatment \
        --exclude "ex-fold0(2020Q2)=2020-04-01..2020-06-30" \
        --exclude "ex-2020H2=2020-07-01..2020-12-31"

The FULL row's paired net diff/CI must reproduce the gated CLI's verdict line
bit-for-bit (same machinery, same seed) — a mismatch means the run dirs
changed since the verdict and BOTH artifacts must be regenerated.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("baseline_dir")
    ap.add_argument("treatment_dir")
    ap.add_argument(
        "--exclude", action="append", default=[], metavar="NAME=START..END",
        help="named date range to EXCLUDE as a sensitivity slice (repeatable)",
    )
    args = ap.parse_args(argv)

    import numpy as np

    # _MIN_CI_WIDTH is module-private but this script must enforce the SAME
    # degenerate-CI rule as the ruler (codex P2 #326: bypassing the guards
    # could commit a fabricated slice verdict) — importing the constant keeps
    # the two enforcement points from drifting.
    from src.core.comparison import (
        _MIN_CI_WIDTH,
        DEFAULT_MIN_PAIRED_DAYS,
        estimate_block_length,
        load_run_daily_series,
        paired_block_bootstrap,
    )

    base = load_run_daily_series(args.baseline_dir)
    treat = load_run_daily_series(args.treatment_dir)
    dates = sorted(set(base.excess) & set(treat.excess))
    if not dates:
        raise SystemExit("FAIL: no shared days between the two runs.")
    dn_all = np.array([treat.excess[d] - base.excess[d] for d in dates])
    dg_all = np.array([treat.gross[d] - base.gross[d] for d in dates])

    slices: list[tuple[str, str, str]] = []
    for spec in args.exclude:
        try:
            name, rng = spec.split("=", 1)
            start, end = rng.split("..", 1)
        except ValueError:
            raise SystemExit(
                f"FAIL: --exclude {spec!r} is not NAME=START..END."
            ) from None
        slices.append((name.strip(), start.strip(), end.strip()))

    print("# paired net + gross deltas (annualized) — ruler machinery, "
          "seed 42, n_boot 10000, acf-decay block length")
    print(f"baseline : {args.baseline_dir}")
    print(f"treatment: {args.treatment_dir}")
    print(f"shared days: {len(dates)} ({dates[0]} .. {dates[-1]})")
    print()
    print("| slice | n | paired NET diff [95% CI] | state | "
          "paired GROSS diff [95% CI] | state |")
    print("|---|---|---|---|---|---|")

    def state(point: float, lo: float, hi: float, *, label: str) -> str:
        if lo <= 0.0 <= hi:
            return "indistinguishable"
        # The ruler's degenerate-CI backstop, verbatim semantics: a
        # DIRECTIONAL state may not rest on a CI narrower than the reporting
        # resolution — refuse loud, never commit a fabricated slice verdict.
        if (hi - lo) <= _MIN_CI_WIDTH:
            raise SystemExit(
                f"FAIL ({label}): directional state would rest on a "
                f"degenerate CI (width {hi - lo:.2e} <= {_MIN_CI_WIDTH:.0e}) "
                "— (near-)constant paired difference or too few days. "
                "Refusing to emit evidence."
            )
        return "treatment_better" if point > 0 else "treatment_worse"

    rows = [("FULL", None)] + [(n, (s, e)) for n, s, e in slices]
    for name, rng in rows:
        if rng is None:
            mask = np.ones(len(dates), dtype=bool)
        else:
            lo_d, hi_d = rng
            mask = np.array([not (lo_d <= d <= hi_d) for d in dates])
        if int(mask.sum()) < DEFAULT_MIN_PAIRED_DAYS:
            # The ruler's min-paired-days guard, mirrored: a handful of days
            # gives a ~zero-width CI — fail loud (mistyped/over-broad slice),
            # never a fabricated row in committed evidence.
            raise SystemExit(
                f"FAIL (slice {name!r}): only {int(mask.sum())} paired day(s) "
                f"remain (< min_paired_days={DEFAULT_MIN_PAIRED_DAYS}) — the "
                "exclusion range is mistyped or removes nearly everything."
            )
        dn, dg = dn_all[mask], dg_all[mask]
        bl = estimate_block_length(dn)
        pn, _, lon, hin = paired_block_bootstrap(dn, bl)
        pg, _, log_, hig = paired_block_bootstrap(dg, bl)
        print(
            f"| {name} | {int(mask.sum())} "
            f"| {pn:+.4f} [{lon:+.4f}, {hin:+.4f}] "
            f"| {state(pn, lon, hin, label=f'{name} net')} "
            f"| {pg:+.4f} [{log_:+.4f}, {hig:+.4f}] "
            f"| {state(pg, log_, hig, label=f'{name} gross')} |"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
