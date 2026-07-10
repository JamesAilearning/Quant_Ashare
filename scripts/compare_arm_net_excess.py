"""Per-arm NET excess vs ZERO — the one-sample auditable evidence generator.

The gated compare CLI emits PAIRED (treatment - baseline) deltas, and
`compare_paired_slices.py` emits paired net/gross slices — but a campaign
whose verdict rests on "is ANY arm's own net excess significantly positive?"
(the 阶段7b DEAD-END exit condition) needs a ONE-SAMPLE net-excess-vs-zero
CI per arm, and the pre-registration requires that generator to be committed
with the results (codex P1 on #339: a decisive claim must be reproducible
from committed tooling, never an ad-hoc harness).

Each arm's daily NET excess (return - bench - cost, already in its
`daily_series`) is bootstrapped against zero with the SAME ruler machinery
`compare_paired_slices.py` uses — shared-day alignment is trivial here (one
series), acf-decay block length, circular moving-block bootstrap, seed 42,
n_boot 10000 — and the SAME fail-loud guards (min paired days; degenerate
CI; non-finite). A one-sample mean-vs-zero is `paired_block_bootstrap` over
the arm's own net series (diff against the implicit zero baseline).

Usage (repo root; each arg is a walk-forward run dir):

    python scripts/compare_arm_net_excess.py \
        output/stage7/daily_h1 output/stage7/daily_h5 \
        output/stage7/weekly_h1 output/stage7/weekly_h5

State per arm: SIG-POSITIVE (CI low > 0), SIG-NEGATIVE (CI high < 0), or
indistinguishable-from-0. Exit 0 always on well-formed runs; a run that
fails a guard exits 1 (never a fabricated CI).
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
    ap.add_argument("run_dirs", nargs="+", help="walk-forward run dirs (one per arm)")
    args = ap.parse_args(argv)

    import numpy as np

    # _MIN_CI_WIDTH is module-private but this script must enforce the SAME
    # degenerate-CI rule as the ruler / compare_paired_slices — importing the
    # constant keeps the enforcement points from drifting (codex P2 #326).
    from src.core.comparison import (
        _MIN_CI_WIDTH,
        DEFAULT_MIN_PAIRED_DAYS,
        estimate_block_length,
        load_run_daily_series,
        paired_block_bootstrap,
    )

    print("# per-arm NET excess vs zero — ruler machinery, seed 42, "
          "n_boot 10000, acf-decay block length")
    print()
    print("| arm | n | net_ann | 95% CI | state |")
    print("|---|---|---|---|---|")

    for run_dir in args.run_dirs:
        series = load_run_daily_series(run_dir)
        dates = sorted(series.excess)
        x = np.array([series.excess[d] for d in dates])
        n = len(x)
        if n < DEFAULT_MIN_PAIRED_DAYS:
            raise SystemExit(
                f"FAIL ({run_dir}): only {n} net-excess day(s) "
                f"(< min_paired_days={DEFAULT_MIN_PAIRED_DAYS}) — too few for "
                "a bootstrap CI; refusing to emit a spurious one."
            )
        point, _se, lo, hi = paired_block_bootstrap(x, estimate_block_length(x))
        if not all(map(math.isfinite, (point, lo, hi))):
            raise SystemExit(
                f"FAIL ({run_dir}): non-finite bootstrap result "
                f"(point={point!r} ci=({lo!r},{hi!r})) — a fold report "
                "carries non-finite daily net values. Refusing to emit."
            )
        if lo <= 0.0 <= hi:
            state = "indistinguishable-from-0"
        else:
            # directional state must not rest on a CI narrower than the
            # reporting resolution (degenerate — the ruler's backstop)
            if (hi - lo) <= _MIN_CI_WIDTH:
                raise SystemExit(
                    f"FAIL ({run_dir}): directional state would rest on a "
                    f"degenerate CI (width {hi - lo:.2e} <= "
                    f"{_MIN_CI_WIDTH:.0e}). Refusing to emit evidence."
                )
            state = "SIG-POSITIVE" if lo > 0 else "SIG-NEGATIVE"
        name = Path(run_dir).name
        print(f"| {name} | {n} | {point:+.4f} | [{lo:+.4f}, {hi:+.4f}] | {state} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
