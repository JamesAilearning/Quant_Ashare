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
import math
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
        DEFAULT_OVERLAP_FLOOR,
        estimate_block_length,
        load_run_daily_series,
        paired_block_bootstrap,
    )

    base = load_run_daily_series(args.baseline_dir)
    treat = load_run_daily_series(args.treatment_dir)
    dates = sorted(set(base.excess) & set(treat.excess))
    if not dates:
        raise SystemExit("FAIL: no shared days between the two runs.")
    # The ruler's overlap refusal, mirrored (codex P2 #326 r2): runs that
    # only overlap on a small biased subset must not receive ANY evidence
    # rows — same formula as compare_runs (intersection / shorter series).
    shorter = min(len(base.excess), len(treat.excess))
    overlap = (len(dates) / shorter) if shorter else 0.0
    if overlap < DEFAULT_OVERLAP_FLOOR:
        raise SystemExit(
            f"FAIL: date overlap {overlap:.1%} (intersection {len(dates)} / "
            f"shorter {shorter}) is below the ruler's floor "
            f"{DEFAULT_OVERLAP_FLOOR:.0%} — a paired comparison would be on "
            "a biased subset; the official ruler would refuse these runs. "
            "Refusing to emit evidence."
        )
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

    def state(lo: float, hi: float, *, label: str) -> str:
        # Verdict SIDE from the CI, never the point estimate — the ruler's
        # rule verbatim (codex P2 #326 r6: a skewed percentile CI can put
        # the point on the other side of zero; a directional label must
        # never contradict its own reported CI).
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
        return "treatment_better" if lo > 0 else "treatment_worse"

    rows = [("FULL", None)] + [(n, (s, e)) for n, s, e in slices]
    for name, rng in rows:
        if rng is None:
            mask = np.ones(len(dates), dtype=bool)
        else:
            lo_d, hi_d = rng
            mask = np.array([not (lo_d <= d <= hi_d) for d in dates])
            if int(mask.sum()) == len(dates):
                # A no-op exclusion (codex P2 #326 r3): zero shared dates
                # matched — a typo'd/out-of-window range would silently emit
                # the FULL comparison under the slice's name, skipping a
                # pre-registered sensitivity check instead of failing loud.
                raise SystemExit(
                    f"FAIL (slice {name!r}): the exclusion range "
                    f"{lo_d}..{hi_d} matches ZERO shared dates — the row "
                    "would just duplicate FULL under a slice name. Fix the "
                    "range (stale window? non-ISO/zero-padded date?)."
                )
        # Slice-level overlap recheck (codex P2 #326 r4): the FULL-run floor
        # can pass while a slice's remaining universe overlaps on a biased
        # subset (each arm's non-shared dates sitting outside the excluded
        # window). Same formula as the ruler, applied to the sliced arms.
        if rng is None:
            kept_base, kept_treat = len(base.excess), len(treat.excess)
        else:
            lo_d, hi_d = rng
            kept_base = sum(
                1 for d in base.excess if not (lo_d <= d <= hi_d))
            kept_treat = sum(
                1 for d in treat.excess if not (lo_d <= d <= hi_d))
        shorter_kept = min(kept_base, kept_treat)
        slice_overlap = (int(mask.sum()) / shorter_kept) if shorter_kept else 0.0
        if slice_overlap < DEFAULT_OVERLAP_FLOOR:
            raise SystemExit(
                f"FAIL (slice {name!r}): after the exclusion the paired days "
                f"cover only {slice_overlap:.1%} of the shorter arm's "
                f"remaining series ({int(mask.sum())} / {shorter_kept}) — "
                f"below the ruler's floor {DEFAULT_OVERLAP_FLOOR:.0%}; the "
                "slice would compare a biased subset. Refusing to emit "
                "evidence."
            )
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
        # Each series gets ITS OWN acf-decay block length (codex P2 #326 r2):
        # gross and net paired differences can carry different
        # autocorrelation (cost differences are low-noise), and reusing the
        # net block for gross could mis-size the gross CI — the number that
        # gates 10d escalation.
        pn, _, lon, hin = paired_block_bootstrap(dn, estimate_block_length(dn))
        pg, _, log_, hig = paired_block_bootstrap(dg, estimate_block_length(dg))
        # The ruler's non-finite refusal, mirrored (codex P2 #326 r5): a
        # malformed report can smuggle NaN through float("NaN") past the
        # JSON parse_constant hook; NaN comparisons are all False, so
        # state() would fall through to a fabricated directional row.
        if not all(map(math.isfinite, (pn, lon, hin, pg, log_, hig))):
            raise SystemExit(
                f"FAIL (slice {name!r}): non-finite bootstrap result "
                f"(net={pn!r} ci=({lon!r},{hin!r}); gross={pg!r} "
                f"ci=({log_!r},{hig!r})) — a fold report carries non-finite "
                "daily values. Fix the run artifacts; refusing to emit "
                "evidence."
            )
        print(
            f"| {name} | {int(mask.sum())} "
            f"| {pn:+.4f} [{lon:+.4f}, {hin:+.4f}] "
            f"| {state(lon, hin, label=f'{name} net')} "
            f"| {pg:+.4f} [{log_:+.4f}, {hig:+.4f}] "
            f"| {state(log_, hig, label=f'{name} gross')} |"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
